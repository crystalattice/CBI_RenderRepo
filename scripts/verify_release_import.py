#!/usr/bin/env python3
"""Verify the included raw-JSON import in disposable state; never install/run it."""
import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from renderrepo.archive import write_archive, package_directory
from renderrepo.capsule import verify
from renderrepo.comfy_extension import PackageUI

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SHA256 = '04e34c77bf2304f9cfea972db0c02e3fef3ffae88e59406e0121a58d7c48b9cd'

async def main():
    raw = (ROOT / 'workflows/image_z_image_turbo.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == EXPECTED_SHA256
    assert raw == (ROOT / 'tests/fixtures/image_z_image_turbo.json').read_bytes()
    graph = json.loads(raw)
    outer = next(n['widgets_values_named']['text'] for n in graph['nodes']
                 if n.get('type') != 'MarkdownNote' and 'text' in n.get('widgets_values_named', {}))
    schema = json.loads((ROOT / 'tests/fixtures/comfy_analysis_object_info.json').read_text())
    with tempfile.TemporaryDirectory(prefix='cbi-rr-import-') as temp:
        target = Path(temp) / 'Comfy'; target.mkdir()
        empty = Path(temp) / 'empty-model-source'; empty.mkdir()
        ui = PackageUI(target, [empty]); ui.target_url = 'http://127.0.0.1:1'
        observed = {'nodes': schema, 'system': {'system': {}, 'devices': []},
                    'errors': {}, 'observed_at': 'offline-fixture', 'server': ui.target_url}
        commands = []; real_command = ui.command
        async def guarded_command(argv, *args, **kwargs):
            assert '--dry-run' in argv, 'Only read-only preparation commands are permitted.'
            commands.append(argv)
            return await real_command(argv, *args, **kwargs)
        try:
            # These are labelled offline schemas, not live target registrations.
            with patch('renderrepo.readiness.snapshot', return_value=observed), \
                 patch.object(ui, 'command', side_effect=guarded_command), \
                 patch('urllib.request.urlopen', side_effect=AssertionError('Network is forbidden in this smoke check.')):
                result = await ui.preview_workflow(raw, 'cbi-z-image-import-check.json')
            c = result['capabilities']; session = ui.get(result['session'])
            assert c['prompts'][0]['value'] == outer and c['prompts'][0]['role'] == 'positive'
            assert len(c['controls']) == 7 and len(c['nodes']) == 10 and 'MarkdownNote' not in c['nodes']
            assert 'Z-Image' in c['purpose'] and 'Generate an image' in c['purpose']
            assert session['package'].joinpath('workflow.json').read_bytes() == raw
            descriptor = verify(session['package'], session['manifest'])
            assert descriptor['profile'] == 'renderrepo' and descriptor['schema_version'] == 1
            names = sorted(a['filename'] for a in result['manifest']['artifacts'])
            assert names == sorted(['z_image_turbo_int8_convrot.safetensors', 'qwen_3_4b_fp4_flux2.safetensors', 'ae.safetensors'])
            assert not result['can_install'], 'Empty target must report missing dependencies, not claim installability.'
            assert not (target / 'user').exists() and not (target / 'models').exists()
            archive = Path(temp) / 'import-roundtrip.rrpkg'; write_archive(session['package'], archive)
            with package_directory(archive) as restored:
                assert (restored / 'workflow.json').read_bytes() == raw
                assert verify(restored, session['manifest'])['sha256'] == descriptor['sha256']
            print(json.dumps({'result': 'PASS', 'workflow_sha256': EXPECTED_SHA256,
                              'literal_outer_prompt_preserved': True, 'controls': 7, 'executable_types': 10,
                              'required_models': names, 'capsule_profile': descriptor['profile'],
                              'original_bytes_preserved_on_import_export': True,
                              'missing_dependencies_reported': True, 'dry_run_commands': len(commands),
                              'downloads': 0, 'installs': 0, 'graph_submissions': 0,
                              'node_evidence': 'minimized offline schema fixture; not live target certification',
                              'execution_hardware_performance': 'untested'}, indent=2))
        finally:
            ui.temporary.cleanup()

if __name__ == '__main__':
    asyncio.run(main())
