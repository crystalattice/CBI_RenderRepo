#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Prepare a workflow-only indexed local source for manual testing in real Comfy."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from renderrepo.cli import main as cli


def prepare(root):
    if root.exists() and any(root.iterdir()):
        raise ValueError('Use a new or empty fixture directory; existing files are never overwritten.')
    publisher = root / 'publisher'
    publisher.mkdir(parents=True)
    graph = {'last_node_id': 1, 'last_link_id': 0, 'nodes': [{
        'id': 1, 'type': 'SaveImage', 'pos': [100, 100], 'size': [300, 100],
        'flags': {}, 'order': 0, 'mode': 0,
        'inputs': [{'name': 'images', 'type': 'IMAGE', 'link': None}],
        'outputs': [], 'properties': {'Node name for S&R': 'SaveImage'},
        'widgets_values': ['rr-repository-gui-tiny']}],
        'links': [], 'groups': [], 'config': {}, 'extra': {}, 'version': 0.4}
    workflow = root / 'workflow.json'
    workflow.write_text(json.dumps(graph, indent=2) + '\n')
    archive = publisher / 'rr-repository-gui-tiny-0.1.0.rrpkg'
    if cli(['pack', str(workflow), '--output', str(archive), '--name', 'rr-repository-gui-tiny',
            '--version', '0.1.0', '--description', 'Repository GUI acceptance: one SaveImage node; no models or artifacts']) != 0:
        raise ValueError('Fixture packaging failed.')
    if cli(['index', str(publisher)]) != 0:
        raise ValueError('Fixture index generation failed.')
    entry = json.loads((publisher / 'index.json').read_text())['packages'][0]
    with zipfile.ZipFile(archive) as packed:
        if set(packed.namelist()) != {'manifest.yaml', 'workflow.json', 'capsule.json'}:
            raise ValueError('Fixture must contain only manifest and workflow.')
    plan = {'source_name': 'rr-gui-acceptance', 'source_path': str(publisher),
            'package': entry, 'workflow_sha256': hashlib.sha256(workflow.read_bytes()).hexdigest(),
            'render': False, 'models': False, 'pip': False, 'provider_calls': False}
    (root / 'fixture.json').write_text(json.dumps(plan, indent=2) + '\n')
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path)
    args = parser.parse_args()
    root = args.directory.expanduser().resolve() if args.directory else Path(tempfile.mkdtemp(prefix='rr-repository-gui-'))
    try:
        result = prepare(root)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
