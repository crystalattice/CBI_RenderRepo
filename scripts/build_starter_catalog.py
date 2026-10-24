#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Build the local starter catalog into a new directory. Never configures RR or Comfy."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from renderrepo.cli import main as cli

ASSETS = Path(__file__).resolve().parents[1] / 'examples/starter-catalog'
PACKAGES = ('rr-starter-resize', 'rr-starter-portrait-crop', 'rr-starter-overlay')


def build(directory):
    directory = directory.expanduser().absolute()
    if directory.exists() or directory.is_symlink():
        raise ValueError('Destination already exists; choose a new directory. Nothing was overwritten.')
    directory.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.rr-starter-', dir=directory.parent) as temporary:
        stage = Path(temporary) / 'catalog'
        stage.mkdir()
        for name in PACKAGES:
            discovery = ASSETS / (name + '.discovery.json')
            meta = json.loads(discovery.read_text())
            graph = ASSETS / (name + '.json')
            inputs = ['rr-starter-base.png']
            if name.endswith('overlay'):
                inputs.append('rr-starter-overlay.png')
            args = ['pack', str(graph), '--output', str(Path(temporary) / name),
                    '--name', name, '--version', '0.1.0', '--description', meta['summary'],
                    '--discovery', str(discovery), '--artifact-dir', str(ASSETS)]
            for image in inputs:
                args.extend(['--bundle-input', image])
            if cli(args):
                raise ValueError('Packing failed: ' + name)
            example = {'rr-starter-resize': 'resized.png',
                       'rr-starter-portrait-crop': 'portrait.png',
                       'rr-starter-overlay': 'combined.png'}[name]
            # Supplementary bytes are part of a new immutable capsule revision.
            from renderrepo.capsule import copy_capsule
            def add_example(folder):
                (folder / 'examples').mkdir()
                shutil.copyfile(ASSETS / 'outputs' / example, folder / 'examples' / example)
            copy_capsule(Path(temporary) / name, stage / (name + '-0.1.0.rrpkg'), edit=add_example)
        shutil.copytree(ASSETS / 'outputs', stage / 'examples')
        if cli(['index', str(stage)]):
            raise ValueError('Index creation failed')
        # Rename only this freshly built tree. Existing destinations are never reused.
        if directory.exists() or directory.is_symlink():
            raise ValueError('Destination appeared during build; nothing was overwritten.')
        stage.rename(directory)
    return directory / 'index.json'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, type=Path, help='new output directory (spaces are supported)')
    args = parser.parse_args()
    try:
        print('Local starter catalog: ' + str(build(args.directory)))
    except (ValueError, OSError) as error:
        parser.error(str(error))


if __name__ == '__main__':
    main()
