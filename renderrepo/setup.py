"""Reviewable, idempotent setup for an existing local Comfy; stdlib bootstrap."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys


def add_arguments(parser):
    parser.add_argument('--comfy-dir', type=Path)
    parser.add_argument('--python', type=Path, help='Comfy interpreter, when not in .venv or venv')
    parser.add_argument('--checkout', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--check', action='store_true', help='verify only; never install or link')
    parser.add_argument('--yes', action='store_true', help='approve the printed setup plan')


def choose(paths, label, interactive=True):
    paths = list(dict.fromkeys(p.absolute() for p in paths if p.exists()))
    if len(paths) == 1:
        return paths[0]
    if not paths:
        raise ValueError(f'No {label} found. Supply --comfy-dir and, if needed, --python explicitly.')
    message = f'Multiple {label} choices:\n' + '\n'.join(f'{i+1}. {p}' for i, p in enumerate(paths))
    if not interactive or not sys.stdin.isatty():
        raise ValueError(message + '\nSelect explicitly; nothing changed.')
    print(message)
    answer = input('Choose a number (blank cancels): ').strip()
    if not answer.isdigit() or not 1 <= int(answer) <= len(paths):
        raise ValueError('No selection; nothing changed.')
    return paths[int(answer)-1]


def choose_comfy(explicit, interactive=True):
    if explicit:
        root = explicit.expanduser().absolute()
        if not (root / 'main.py').is_file():
            raise ValueError(f'Comfy main.py not found: {root}')
        return root
    home = Path.home()
    candidates = [Path.cwd(), Path.cwd()/'ComfyUI', Path.cwd().parent/'ComfyUI',
                  home/'ComfyUI', home/'Apps/ComfyUI', home/'PycharmProjects/ComfyUI']
    return choose([p for p in candidates if (p/'main.py').is_file()], 'Comfy installation', interactive)


def same_files(source, target):
    if not target.is_dir():
        return False
    return all((target/p.relative_to(source)).is_file() and
               (target/p.relative_to(source)).read_bytes() == p.read_bytes()
               for p in source.rglob('*') if p.is_file() and '__pycache__' not in p.parts
               and p.suffix in {'.py', '.js', '.css', '.json', '.html'})


def probe(python):
    script = '''import sys,json,importlib.util
s=importlib.util.find_spec('renderrepo')
error=None
if s:
 try: import renderrepo.comfy_extension
 except ImportError as e: error=str(e)
print(json.dumps({'version':list(sys.version_info[:2]),'prefix':sys.prefix,'module':s.origin if s else None,'pip':importlib.util.find_spec('pip') is not None,'import_error':error}))'''
    result = subprocess.run([str(python), '-I', '-c', script], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError(f'Cannot run Comfy Python {python}: {result.stderr.strip()}')
    return json.loads(result.stdout)


def setup_comfy(args):
    checkout = args.checkout.expanduser().resolve()
    if not (checkout/'pyproject.toml').is_file() or not (checkout/'extensions/comfyui_renderrepo').is_dir():
        raise ValueError('Supply --checkout pointing to the RenderRepo checkout (pyproject.toml and extensions).')
    comfy = choose_comfy(args.comfy_dir, not args.check)
    python = args.python.expanduser().absolute() if args.python else choose(
        [comfy/'.venv/bin/python', comfy/'venv/bin/python'], 'Comfy Python environment', not args.check)
    info = probe(python)
    if info['version'] < [3, 13]:
        raise ValueError('RenderRepo needs Comfy Python 3.13+. This setup will not upgrade Comfy or Python.')
    extension = checkout/'extensions/comfyui_renderrepo'
    if (comfy/'custom_nodes').is_symlink() and not (comfy/'custom_nodes').resolve().is_relative_to(comfy.resolve()):
        raise ValueError('custom_nodes points outside this Comfy installation. Inspect it and connect RR manually; setup will not write through that link.')
    link = comfy/'custom_nodes/comfyui_renderrepo'
    connected = link.is_symlink() and link.resolve() == extension or (link.is_dir() and same_files(extension, link))
    if (link.exists() or link.is_symlink()) and not connected:
        raise ValueError(f'Unrelated or different extension exists at {link}. Inspect/preserve it; setup will not overwrite it.')
    installed = bool(info['module'] and not info.get('import_error') and same_files(checkout/'renderrepo', Path(info['module']).parent))
    print(f'Comfy: {comfy}\nPython: {python}\nRR checkout: {checkout}')
    print('Environment: ' + ('RR already matches; no install needed.' if installed else 'Install RR and its PyYAML requirement only; no Comfy/Torch/CUDA/model upgrades.'))
    print('Extension: ' + ('already connected; keep it.' if connected else f'create link {link} → {extension}'))
    if args.check:
        print('Check passed.' if installed and connected else 'Setup needed. Rerun without --check to review/apply.')
        return 0 if installed and connected else 1
    command = None
    if not installed:
        uv = shutil.which('uv')
        if uv:
            command = [uv, 'pip', 'install', '--python', str(python), str(checkout)]
        elif info['pip']:
            command = [str(python), '-m', 'pip', 'install', str(checkout)]
        else:
            raise ValueError('Comfy Python has no pip and uv is unavailable. Install uv separately, then rerun this command; nothing changed.')
        print('Run: ' + subprocess.list2cmdline(command))
    sys.stdout.flush()
    if installed and connected:
        print('Open Comfy and click RenderRepo. No setup changes needed.')
        return 0
    if not args.yes and input('Apply these setup changes? [y/N] ').strip().casefold() not in {'y', 'yes'}:
        print('Cancelled; nothing changed.')
        return 1
    if command:
        result = subprocess.run(command)
        if result.returncode:
            raise ValueError('RR installation failed. Inspect the installer output and rerun the same setup; extension was not replaced.')
    if not connected:
        link.parent.mkdir(parents=True, exist_ok=True)
        # symlink creation refuses destinations that appeared after the plan.
        link.symlink_to(extension, target_is_directory=True)
    verified = probe(python)
    if not verified['module'] or verified.get('import_error') or not same_files(extension, link):
        raise ValueError('Setup verification failed. Preserve the paths above and rerun --check.')
    print('Restart Comfy using its usual launcher, reload its browser page, then click RenderRepo.')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    try:
        return setup_comfy(parser.parse_args(argv))
    except (ValueError, OSError, subprocess.SubprocessError, EOFError) as error:
        print(f'Setup: {error}', file=sys.stderr)
        return 2
