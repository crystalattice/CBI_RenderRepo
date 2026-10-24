"""Pinned Git custom-node acquisition; no imports or environment changes before consent."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import urlsplit

from .runtime import git_provenance
from .installation_history import inherited_worker_fds


def validate_nodes(entries):
    if not isinstance(entries, list):
        raise ValueError('runtime.custom_nodes must be a list')
    names = set()
    for node in entries:
        if not isinstance(node, dict) or set(node) - {'name', 'source', 'revision'}:
            raise ValueError('custom-node requirements allow name, source, revision only')
        name = node.get('name')
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]*', name) or name.endswith('.disabled'):
            raise ValueError('custom-node name must be a safe directory name')
        if name in names:
            raise ValueError('duplicate custom-node requirement')
        names.add(name)
        if not isinstance(node.get('revision'), str) or not re.fullmatch(r'[0-9a-f]{40}', node['revision']):
            raise ValueError(f'{name}: exact 40-character Git commit required')
        source = node.get('source')
        if not isinstance(source, str) or not source or source.startswith('-'):
            raise ValueError(f'{name}: Git source required')
        parts = urlsplit(source)
        if parts.scheme:
            if parts.scheme not in {'https', 'http', 'ssh', 'file'} or parts.query or parts.fragment:
                raise ValueError(f'{name}: unsupported Git source')
            if parts.password or (parts.username and parts.scheme != 'ssh'):
                raise ValueError('Git sources must not embed credentials')
            if parts.scheme != 'file' and not parts.hostname:
                raise ValueError('Git source requires a hostname')
            if parts.scheme == 'file' and parts.netloc not in {'', 'localhost'}:
                raise ValueError('file Git sources must be local')
        elif not Path(source).is_absolute():
            raise ValueError('local Git sources must be absolute paths')
    return entries


def export_nodes(args, captured):
    requested = getattr(args, 'custom_node', None) or []
    overrides = {}
    for entry in getattr(args, 'node_source', None) or []:
        name, sep, source = entry.partition('=')
        if not sep or name not in requested or name in overrides:
            raise ValueError('--node-source must name one selected custom node')
        overrides[name] = source
    result = []
    for name in requested:
        node = next((n for n in captured['custom_nodes'] if n['name'] == name), None)
        if not node or node.get('dirty') is not False:
            raise ValueError(f'{name}: selected custom node must be a clean Git checkout')
        result.append({'name': name, 'source': overrides.get(name) or node.get('source'),
                       'revision': node.get('revision')})
    return validate_nodes(result)


def git(directory, *args, timeout=60):
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
    command = ['git', '-c', 'core.hooksPath=' + os.devnull, '-c', 'protocol.ext.allow=never',
               '-c', 'protocol.file.allow=always', '-c', 'init.templateDir=']
    if directory is not None:
        command += ['-C', str(directory)]
    try:
        fds = inherited_worker_fds()
        run = subprocess.run(command + list(args), env=env, text=True, capture_output=True, timeout=timeout,
                             **({'pass_fds': fds} if fds else {}))
    except subprocess.TimeoutExpired as error:
        raise ValueError('custom-node Git operation timed out') from error
    if run.returncode:
        raise ValueError('custom-node Git operation failed: ' + run.stderr.strip())
    return run.stdout.strip()


def plan_nodes(manifest, args, root):
    entries = validate_nodes(manifest.get('runtime', {}).get('custom_nodes', []))
    if entries and not getattr(args, 'install_nodes', False):
        raise ValueError('package declares custom nodes; pass --install-nodes to approve a code-installation plan')
    if getattr(args, 'comfy_python', None) and not getattr(args, 'install_nodes', False):
        raise ValueError('--comfy-python requires --install-nodes')
    python = getattr(args, 'comfy_python', None)
    if python:
        python = python.expanduser().absolute()
        if not python.is_file() or not os.access(python, os.X_OK):
            raise ValueError('--comfy-python must be an executable Python path')
    folder = root / 'custom_nodes'
    if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
        raise ValueError('custom_nodes must be a real directory')
    plan = []
    for node in entries:
        target = folder / node['name']
        existing = target.exists() or target.is_symlink()
        if existing:
            if target.is_symlink() or not target.is_dir():
                raise ValueError(f'preserving incompatible custom-node placement: {target}')
            actual = git_provenance(target)
            if actual['revision'] != node['revision'] or actual['dirty'] is not False:
                raise ValueError(f'preserving modified or different custom-node checkout: {target}')
        print(f"{'REUSE CODE' if existing else 'INSTALL CODE'}: {node['name']} @ {node['revision']} from {node['source']}")
        print(f'  Python requirements.txt: install if present using {python}' if python else
              '  Python dependencies: unchanged; supply --comfy-python to install requirements.txt')
        plan.append(dict(node, target=target, existing=existing, python=python))
    if plan:
        print('Custom nodes execute Python in Comfy. pip may change shared environment packages; removal will not uninstall them.')
    return plan


def acquire_node(item, timeout):
    target = item['target']
    if item['existing']:
        actual = git_provenance(target)
        if actual['revision'] != item['revision'] or actual['dirty'] is not False:
            raise ValueError('custom node changed after approval')
        return
    if target.parent.is_symlink():
        raise ValueError('custom-node parent changed to a symlink after approval')
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent, prefix='.renderrepo-node-') as temporary:
        stage = Path(temporary) / 'checkout'
        git(None, 'clone', '--no-checkout', '--no-hardlinks', '--', item['source'], str(stage), timeout=timeout)
        git(stage, 'checkout', '--detach', item['revision'], timeout=timeout)
        if git(stage, 'rev-parse', 'HEAD') != item['revision']:
            raise ValueError('custom-node checkout revision mismatch')
        if (stage / '.gitmodules').exists():
            raise ValueError('custom-node submodules require explicit manual setup')
        requirements = stage / 'requirements.txt'
        if requirements.is_symlink():
            raise ValueError('custom-node requirements.txt must not be a symlink')
        if target.exists() or target.is_symlink():
            raise ValueError('custom-node destination appeared after approval; preserving it')
        stage.rename(target)


def install_requirements(item, timeout):
    requirements = item['target'] / 'requirements.txt'
    if requirements.is_symlink():
        raise ValueError('custom-node requirements.txt must not be a symlink')
    if not requirements.exists():
        return 'none'
    if not item['python']:
        print(f"PYTHON DEPENDENCIES UNVERIFIED: {item['name']}; requirements.txt present")
        return 'unverified'
    if not requirements.is_file():
        raise ValueError('requirements.txt must be a file')
    try:
        fds = inherited_worker_fds()
        run = subprocess.run([str(item['python']), '-m', 'pip', 'install', '-r', str(requirements)],
                             cwd=item['target'], timeout=timeout, check=False,
                             **({'pass_fds': fds} if fds else {}))
    except subprocess.TimeoutExpired as error:
        raise ValueError('custom-node pip operation timed out; environment may be partially changed') from error
    if run.returncode:
        raise ValueError('custom-node pip failed; environment may be partially changed')
    return 'installed'
