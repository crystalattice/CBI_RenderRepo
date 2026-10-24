"""Read a manifest, index local filenames, verify candidates, print results."""

import argparse
from contextlib import ExitStack
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import sys
import subprocess

import yaml


def read_yaml(path):
    with path.open(encoding="utf-8") as stream:
        value = yaml.safe_load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a YAML mapping")
    return value


def require_keys(value, allowed, required, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label}: expected a mapping")
    if set(value) - allowed or required - set(value):
        raise ValueError(f"{label}: unknown or missing fields")


def load_package(package):
    manifest_path = package / "manifest.yaml" if package.is_dir() else package
    manifest = read_yaml(manifest_path)
    require_keys(manifest, {"schema_version", "name", "description", "discovery", "capabilities", "workflow", "artifacts", "inputs", "runtime", "unresolved", "version"},
                 {"schema_version", "name", "workflow", "artifacts"}, "manifest")
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 0:
        raise ValueError("schema_version must be 0")
    if not isinstance(manifest["name"], str) or not manifest["name"].strip():
        raise ValueError("name must be a nonempty string")
    if 'version' in manifest and (not isinstance(manifest['version'], str) or not manifest['version'].strip()):
        raise ValueError('version must be a nonempty string; quote YAML version numbers')
    if not isinstance(manifest.get('description', ''), str):
        raise ValueError('description must be a string')
    from .discovery import validate_discovery
    validate_discovery(manifest.get("discovery", {}))
    if 'capabilities' in manifest:
        from .capabilities import validate_summary
        validate_summary(manifest['capabilities'])
    workflow_name = manifest["workflow"]
    if not isinstance(workflow_name, str) or not workflow_name:
        raise ValueError("workflow must be a relative JSON file path")
    root = manifest_path.parent.resolve()
    workflow = (root / workflow_name).resolve()
    if Path(workflow_name).is_absolute() or not workflow.is_relative_to(root):
        raise ValueError("workflow must remain inside the package")
    with workflow.open(encoding="utf-8") as stream:
        graph = json.load(stream)
    # Accept exported editor graphs or API prompt graphs without interpreting topology.
    if not isinstance(graph, dict) or not graph:
        raise ValueError("workflow must contain a ComfyUI JSON object")
    if "nodes" in graph:
        if not isinstance(graph["nodes"], list) or not graph["nodes"]:
            raise ValueError("workflow nodes must be a nonempty list")
    elif not all(isinstance(node, dict) and isinstance(node.get("class_type"), str)
                 and isinstance(node.get("inputs"), dict) for node in graph.values()):
        raise ValueError("workflow must be an editor graph or API prompt graph")
    artifacts = manifest["artifacts"]
    if not isinstance(artifacts, list):
        raise ValueError("artifacts must be a list")
    seen = set()
    for artifact in artifacts:
        require_keys(artifact, {"filename", "size_bytes", "sha256", "sources", "target"},
                     {"filename"}, "artifact")
        name = artifact["filename"]
        if (not isinstance(name, str) or not name or name in {".", ".."}
                or "/" in name or "\\" in name or "\0" in name or name in seen):
            raise ValueError("artifact filenames must be unique, nonempty basenames")
        seen.add(name)
        if "size_bytes" in artifact:
            size = artifact["size_bytes"]
            if type(size) is not int or size < 0:
                raise ValueError(f"{name}: size_bytes must be a nonnegative integer")
        if "sha256" in artifact:
            digest = artifact["sha256"]
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                raise ValueError(f"{name}: sha256 must contain 64 hexadecimal characters")
        if "target" in artifact:
            from .pack import relative_path
            relative_path(artifact["target"])
            if not artifact["target"].startswith(("models/", "input/")):
                raise ValueError("target must start with models/ or input/")
            if Path(artifact["target"]).name != name:
                raise ValueError("target basename must match filename")
        sources = artifact.get("sources", [])
        if not isinstance(sources, list):
            raise ValueError(f"{name}: sources must be a list")
        for source in sources:
            require_keys(source, {"uri"}, {"uri"}, "source")
            if not isinstance(source["uri"], str) or not source["uri"].strip():
                raise ValueError(f"{name}: source uri must be a nonempty string")
    from .pack import relative_path
    if not isinstance(manifest.get("inputs", []), list):
        raise ValueError("inputs must be a list")
    for slot in manifest.get("inputs", []):
        require_keys(slot, {"filename", "kind", "mode"}, {"filename", "kind", "mode"}, "input")
        relative_path(slot["filename"])
        if slot["kind"] not in {"image", "video"} or slot["mode"] not in {"user", "bundled"}:
            raise ValueError("invalid input kind or mode")
    if not isinstance(manifest.get("inputs", []), list):
        raise ValueError("inputs must be a list")
    if not isinstance(manifest.get("unresolved", []), list) or not all(
            isinstance(x, str) for x in manifest.get("unresolved", [])):
        raise ValueError("unresolved must be a list of strings")
    if not isinstance(manifest.get("runtime", {}), dict):
        raise ValueError("runtime must be a mapping")
    from .readiness import validate_requirements
    validate_requirements(manifest.get('runtime', {}).get('requirements', {}))
    from .nodes import validate_nodes
    validate_nodes(manifest.get('runtime', {}).get('custom_nodes', []))
    captured = manifest.get('runtime', {}).get('local_installation')
    if captured is not None:
        require_keys(captured, {'comfy', 'custom_nodes'}, {'comfy', 'custom_nodes'}, 'local_installation')
        if not isinstance(captured['comfy'], dict) or not isinstance(captured['custom_nodes'], list):
            raise ValueError('captured runtime requires a Comfy mapping and custom_nodes list')
        for node in captured['custom_nodes']:
            if not isinstance(node, dict) or not isinstance(node.get('name'), str) or not node['name']:
                raise ValueError('captured custom nodes require nonempty names')

    from .capsule import verify
    verify(root, manifest)
    return manifest


def artifact_directories(args):
    if args.artifact_dir:
        paths = args.artifact_dir
    elif args.config:
        config = read_yaml(args.config)
        require_keys(config, {"artifact_dirs"}, {"artifact_dirs"}, "config")
        paths = config["artifact_dirs"]
        if not isinstance(paths, list) or not all(isinstance(p, str) and p for p in paths):
            raise ValueError("artifact_dirs must be a list of nonempty paths")
        paths = [args.config.resolve().parent / Path(p).expanduser() for p in paths]
    else:
        paths = [p for p in os.environ.get("RENDERREPO_ARTIFACT_DIRS", "").split(os.pathsep) if p]
    directories = []
    for value in paths:
        path = Path(value).expanduser().resolve()
        if not path.is_dir():
            raise ValueError(f"artifact directory does not exist: {path}")
        if path not in directories:
            directories.append(path)
    return directories


def inspect_artifacts(artifacts, directories):
    index = {artifact["filename"]: [] for artifact in artifacts}
    def scan_error(error):
        raise error
    for directory in directories:
        for parent, dirs, files in os.walk(directory, onerror=scan_error, followlinks=False):
            dirs.sort()
            for name in sorted(files):
                if name in index:
                    index[name].append(Path(parent) / name)
    results = []
    for artifact in artifacts:
        name = artifact["filename"]
        failures = []
        found = None
        for path in index[name]:
            size = path.stat().st_size
            if "size_bytes" in artifact and size != artifact["size_bytes"]:
                failures.append(f"{path}: size {size}, expected {artifact['size_bytes']}")
                continue
            if "sha256" in artifact:
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                if digest != artifact["sha256"].lower():
                    failures.append(f"{path}: sha256 {digest}, expected {artifact['sha256'].lower()}")
                    continue
            found = path
            break
        status = "FOUND" if found is not None else "HASH MISMATCH" if failures else "MISSING"
        results.append({"filename": name, "status": status,
                        "path": str(found) if found else None, "failures": failures})
    return results


def _main(argv, stack):
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == 'test':
        from .local_test import main
        return main(argv[1:])
    parser = argparse.ArgumentParser(prog="renderrepo")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("test", help="prepare, explicitly approve, observe or stop a bounded local starter test")
    for command, help_text in (("inspect", "verify required artifacts in local directories"),
                               ("install", "ask, then download missing artifacts")):
        subparser = commands.add_parser(command, help=help_text)
        subparser.add_argument("package", help="package directory or manifest.yaml")
        subparser.add_argument("--artifact-dir", action="append", help="recursive search root; repeatable")
        subparser.add_argument("--config", type=Path, help="YAML file containing artifact_dirs")
        if command == "install":
            destination = subparser.add_mutually_exclusive_group()
            destination.add_argument("--destination", type=Path, help="directory for new files")
            destination.add_argument("--comfy-dir", type=Path, help="existing ComfyUI installation")
            subparser.add_argument("--input-dir", type=Path, help="override Comfy input directory")
            subparser.add_argument("--model-dir", type=Path, help="shared model root for Comfy placement")
            subparser.add_argument("--install-nodes", action="store_true", help="include pinned custom-node code in the approval plan")
            subparser.add_argument("--comfy-python", type=Path, help="explicit Comfy Python executable for node requirements.txt")
            subparser.add_argument("--comfy-url", help="run doctor after installation; restart may still be required")
            subparser.add_argument("--workflow-dir", type=Path, help="override Comfy workflow directory")
            subparser.add_argument("--source", action="append", help="manual FILENAME=URL or FILENAME=PATH")
            subparser.add_argument("--dry-run", action="store_true", help="show Comfy installation plan without acquisition or placement")
            subparser.add_argument("--yes", action="store_true", help="explicitly approve the displayed plan")
            subparser.add_argument("--timeout", type=int, default=60, help="network I/O timeout in seconds")
            subparser.add_argument('--rate-limit', type=float, default=5, help='transfer cap in MB/s; default 5, 0 means unlimited')
            subparser.add_argument('--download-control', type=Path, help=argparse.SUPPRESS)
            subparser.add_argument('--plan-json', action='store_true', help=argparse.SUPPRESS)
            subparser.add_argument('--approved-scope', type=Path, help=argparse.SUPPRESS)
            subparser.add_argument('--repository-info', type=Path, help=argparse.SUPPRESS)
            subparser.add_argument('--repo', help='select configured package repository')
            subparser.add_argument('--version', help='select numeric dotted package version')
            subparser.add_argument('--sources', type=Path, default=Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'renderrepo/sources.yaml')
            subparser.add_argument('--cache-dir', type=Path, default=Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'renderrepo')
    pack = commands.add_parser("pack", help="create a package from a ComfyUI export")
    pack.add_argument("workflow", type=Path)
    pack.add_argument("--output", type=Path, required=True)
    pack.add_argument("--name")
    pack.add_argument("--predecessor", type=Path, help="previous package/capsule; link this changed export as a new revision")
    pack.add_argument("--runtime-requirements", type=Path, help="JSON explicit runtime requirements; not captured provenance")
    pack.add_argument("--discovery", type=Path, help="JSON file with optional task discovery metadata")
    pack.add_argument('--description', default='', help='short package description for repository discovery')
    pack.add_argument("--version", default="0.1.0", help="package version string")
    pack.add_argument("--artifact-dir", action="append")
    pack.add_argument("--config", type=Path)
    pack.add_argument("--comfy-dir", type=Path, help="capture local Comfy/custom-node provenance")
    pack.add_argument("--custom-node", action="append", help="select a clean Git custom-node directory to require; repeatable")
    pack.add_argument("--node-source", action="append", help="selected NAME=Git URL or absolute local Git path")
    pack.add_argument("--bundle-input", action="append", help="detected input path to bundle; repeatable")
    capsule = commands.add_parser('capsule', help='explicitly copy/migrate an RR package to a JSON S4 capsule')
    capsule.add_argument('package', type=Path)
    capsule.add_argument('--output', type=Path, required=True)
    for command in ('list', 'remove'):
        subparser = commands.add_parser(command, help='list installed packages' if command == 'list' else 'remove owned workflow placements')
        subparser.add_argument('--comfy-dir', type=Path, required=True)
        if command == 'remove':
            subparser.add_argument('name')
            subparser.add_argument('--yes', action='store_true')
    doctor = commands.add_parser('doctor', help='check running Comfy node and model capabilities')
    doctor.add_argument('package', type=Path)
    doctor.add_argument('--comfy-url', required=True, help='native ComfyUI URL, not API v2 proxy URL')
    doctor.add_argument('--comfy-dir', type=Path)
    doctor.add_argument('--artifact-dir', action='append')
    doctor.add_argument('--config', type=Path)
    doctor.add_argument('--timeout', type=int, default=60)
    readiness = commands.add_parser('readiness', help='read-only local readiness; never downloads or executes')
    readiness.add_argument('package', type=Path)
    readiness.add_argument('--comfy-url', required=True)
    readiness.add_argument('--comfy-dir', type=Path)
    readiness.add_argument('--input-dir', type=Path, help='configured Comfy input folder override')
    readiness.add_argument('--artifact-dir', action='append')
    readiness.add_argument('--config', type=Path)
    readiness.add_argument('--timeout', type=int, default=5)
    readiness.add_argument('--json', action='store_true')
    index = commands.add_parser('index', help='publish an index for a directory of .rrpkg archives')
    index.add_argument('directory', type=Path)
    index.add_argument('--output', type=Path)
    source = commands.add_parser('source', help='manage configured package repositories')
    actions = source.add_subparsers(dest='action', required=True)
    for action in ('add', 'remove', 'list', 'enable', 'disable', 'official'):
        action_parser = actions.add_parser(action)
        if action not in {'list', 'official'}:
            action_parser.add_argument('name')
        if action in {'add', 'official'}:
            action_parser.add_argument('uri', **({'nargs':'?', 'default':''} if action == 'official' else {}))
        action_parser.add_argument('--sources', type=Path, default=Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'renderrepo/sources.yaml')
        action_parser.add_argument('--cache-dir', type=Path, default=Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'renderrepo')
    for command in ('update', 'search', 'updates'):
        subparser = commands.add_parser(command, aliases={'update':['refresh'], 'search':['packages'], 'updates':[]}[command])
        subparser.add_argument('--sources', type=Path, default=Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'renderrepo/sources.yaml')
        subparser.add_argument('--cache-dir', type=Path, default=Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'renderrepo')
        subparser.add_argument('--repo', help='select one configured source')
        if command == 'search':
            subparser.add_argument('query', nargs='?', default='')
        else:
            subparser.add_argument('--comfy-dir', type=Path, required=command == 'updates')
        if command == 'update':
            subparser.add_argument('--timeout', type=int, default=60)
    setup = commands.add_parser("setup", help="connect RR to an existing local Comfy installation")
    from .setup import add_arguments
    add_arguments(setup)
    args = parser.parse_args(argv)
    try:
        if args.command == "setup":
            from .setup import setup_comfy
            return setup_comfy(args)
        if hasattr(args, "package"):
            args.package_text = args.package
            args.package = Path(args.package)
        if args.command in {'index', 'source', 'update', 'refresh', 'search', 'packages', 'updates'}:
            from .repository import build_index, manage_sources, refresh, search, available_updates
            return {'index': build_index, 'source': manage_sources, 'update': refresh, 'refresh': refresh,
                    'search': search, 'packages': search, 'updates': available_updates}[args.command](args)
        if args.command == 'install':
            from .repository import acquire_package
            from .setup import choose_comfy
            if not args.comfy_dir and not args.destination:
                args.comfy_dir = choose_comfy(None)
            from .package_link import is_link, retrieve_link
            if is_link(args.package_text):
                args.package = retrieve_link(args.package_text, args.cache_dir, args.timeout)
            by_name = not is_link(args.package_text) and (args.repo or (not args.package.exists() and len(args.package.parts) == 1
                                   and args.package.suffix not in {'.rrpkg', '.yaml', '.json'}))
            if by_name:
                args.package = acquire_package(args)
                if args.package is None:
                    return 1
            elif args.version:
                raise ValueError('--version applies only to repository installation by name')
        if args.command in {'list', 'remove'}:
            from .receipts import list_installed, remove_package
            return list_installed(args) if args.command == 'list' else remove_package(args)
        if args.command == 'capsule':
            from .capsule import copy_capsule
            copy_capsule(args.package, args.output)
            return 0
        if args.command == "pack":
            from .pack import pack_workflow
            return pack_workflow(args, artifact_directories(args))
        from .archive import package_directory
        args.package = stack.enter_context(package_directory(args.package))
        manifest = load_package(args.package)
        if args.command == 'install' and isinstance(args.repository_info, Path):
            from .repository import validate_provenance
            args.repository_info = validate_provenance(json.loads(args.repository_info.read_text()))
        if getattr(args, 'expected_package', None) and (
                manifest['name'] != args.expected_package['name'] or
                manifest.get('version', '0') != args.expected_package['version']):
            raise ValueError('archive manifest identity does not match the repository index')
        args.package_root = (args.package if args.package.is_dir() else args.package.parent).resolve()
        if args.command == 'readiness':
            if args.timeout <= 0:
                raise ValueError('timeout must be positive')
            if args.comfy_dir and not args.comfy_dir.is_dir():
                raise ValueError('Comfy directory does not exist')
            from .readiness import command
            return command(manifest, args, artifact_directories(args))
        if args.command == 'doctor':
            from .runtime import doctor
            return doctor(manifest, args, artifact_directories(args))
        if args.command == "install" and args.comfy_dir:
            from .comfy import install_comfy
            return install_comfy(manifest, args, artifact_directories(args))
        if args.command == "install" and (args.model_dir or args.workflow_dir or args.input_dir):
            raise ValueError("--model-dir, --input-dir, and --workflow-dir require --comfy-dir")
        if args.command == "install" and (args.install_nodes or args.comfy_python or args.comfy_url or args.dry_run):
            raise ValueError("node installation and runtime checks require --comfy-dir")
        directories = artifact_directories(args)
        if args.command == "install":
            destination = args.destination.expanduser().resolve()
            if destination.is_dir() and destination not in directories:
                directories.append(destination)
        results = inspect_artifacts(manifest["artifacts"], directories)
    except KeyboardInterrupt:
        print("Interrupted; incomplete transfer removed.", file=sys.stderr)
        return 130
    except (OSError, ValueError, yaml.YAMLError, http.client.HTTPException, subprocess.SubprocessError, EOFError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(f"Package: {manifest['name']}")
    if "runtime" in manifest:
        print("Runtime/node compatibility unverified.")
    for issue in manifest.get("unresolved", []):
        print(f"UNRESOLVED: {issue}")
    for slot in manifest.get("inputs", []):
        print(f"INPUT ({slot['mode']}): {slot['filename']}")
    for result in results:
        detail = f" -> {result['path']}" if result["path"] else ""
        print(f"{result['status']}: {result['filename']}{detail}")
        for failure in result["failures"]:
            print(f"  rejected: {failure}")
    satisfied = all(result["status"] == "FOUND" for result in results)
    if args.command == "install":
        from .download import install_artifacts
        try:
            return install_artifacts(manifest, results, args)
        except (OSError, ValueError) as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 2
        except KeyboardInterrupt:
            print("Interrupted; incomplete download removed.", file=sys.stderr)
            return 130
    print("Artifacts satisfied." if satisfied else "Artifacts not satisfied.")
    return 0 if satisfied else 1


def main(argv=None):
    with ExitStack() as stack:
        return _main(argv, stack)

