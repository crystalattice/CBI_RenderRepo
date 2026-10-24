"""Reuse the qualified Comfy v2 transport and CBI auth from comfy_cloud_mcp.

No provider routes or credentials are defined here. Import is deferred until run.
"""
import importlib
import mimetypes
from pathlib import Path
import sys

DEFAULT_DOWNLOAD_HOSTS = ('storage.googleapis.com',)


class Rejected(Exception):
    definitely_rejected = True


class ComfyProvider:
    def check(self, config):
        repository = Path(config['adapter_repo']).resolve()
        if not (repository / 'renderlab_comfy_v2.py').is_file():
            raise ValueError('Existing comfy_cloud_mcp checkout required at adapter_repo.')
        # Explicit trusted local checkout, not a package downloaded from the experiment.
        sys.path.insert(0, str(repository))
        wire = importlib.import_module('renderlab_comfy_v2')
        account = importlib.import_module('renderlab_comfy_account')
        if Path(wire.__file__).resolve().parent != repository or Path(account.__file__).resolve().parent != repository:
            raise ValueError('Adapter already imported from a different checkout.')
        self.wire = wire
        self.http = wire.V2HTTP(wire.V2Config(base_url=config['base_url'], download_hosts=tuple(config.get('download_hosts', []))))
        try:
            account.cbi_credential()  # Fail locally before any request when missing/unresolved.
            self.identity = account.verify_cbi_account()
        except ValueError as error:
            raise ValueError(f'Comfy CBI preflight: {error}. Resolve COMFY_CLOUD_API_KEY_CBI through your existing secret setup; no render was submitted.') from None
        except Exception as error:
            raise ValueError(f'Comfy CBI account verification failed ({type(error).__name__}); verify the resolved CBI key and network access. No render was submitted.') from None
        # Existing account pin, no fallback key.

    def prepare(self, graph, directory, bindings, settings):
        # Managed Cloud offers inventory, not arbitrary node/pip installation.
        # A capability failure is Unknown; known absent nodes block asset upload.
        self.target_evidence = {'state': 'unknown', 'installation_supported': False}
        try:
            inventory, _ = self.http.request('GET', '/api/object_info')
            if not isinstance(inventory, dict) or not all(isinstance(v, dict) for v in inventory.values()):
                raise ValueError('Malformed node inventory')
        except Exception as error:
            self.target_evidence['reason'] = type(error).__name__
        else:
            missing = sorted({n['class_type'] for n in graph.values()} - inventory.keys())
            from ..pack import LOADERS
            models = []
            for node in graph.values():
                spec = inventory.get(node['class_type'], {}).get('input', {})
                for field, _, _ in LOADERS.get(node['class_type'], []):
                    selected = node['inputs'].get(field)
                    choices = spec.get('required', {}).get(field, spec.get('optional', {}).get(field))
                    choices = choices[0] if isinstance(choices, list) and choices else None
                    state = 'unknown' if not isinstance(selected, str) or not isinstance(choices, list) else 'available' if selected in choices else 'missing'
                    models.append({'node': node['class_type'], 'field': field, 'selection': selected, 'state': state})
            unavailable_models = [m for m in models if m['state'] == 'missing']
            self.target_evidence.update(state='missing' if missing else 'available', missing_nodes=missing,
                                        models=models, limitation='Node and advertised model visibility only; file identity, memory fit and execution are not guaranteed.')
            if unavailable_models:
                self.target_evidence['state'] = 'missing'
            elif not missing and any(m['state'] == 'unknown' for m in models):
                self.target_evidence['state'] = 'unknown'
            if missing or unavailable_models:
                unavailable = missing + [m['field'] + '=' + str(m['selection']) for m in unavailable_models]
                self.target_evidence['action'] = ('Requirements unavailable on selected managed Cloud target: ' + ', '.join(unavailable)
                                 + '. RR cannot install arbitrary custom nodes or Python there. Choose a supported workflow or explicitly select another target; no assets uploaded.')
                raise ValueError(self.target_evidence['action'])
        for slot, ref in settings.items():
            if slot.startswith('reference:'):
                content = (directory / 'references' / ref['file']).read_bytes()
                media = mimetypes.guess_type(ref['file'])[0]
                self.wire.validate_image(content, media)
                asset = self.http.upload(content, media)
                for node, field in bindings[slot]:
                    graph[node]['inputs'][field] = asset
        return graph

    def submit(self, graph, token):
        try:
            data, _ = self.http.submit(graph, token)
            return data['id']
        except self.wire.V2Error as e:
            if not e.uncertain:
                raise Rejected(e.category) from None
            raise

    def status(self, provider_id):
        provider_id = self.wire.identifier(provider_id)
        data, _ = self.http.request('GET', '/api/v2/jobs/' + provider_id)
        if data.get('id') != provider_id or data.get('status') not in self.wire.STATES:
            raise ValueError('Provider returned mismatched job identity or unknown state.')
        milliseconds = (data.get('metrics') or {}).get('execution_ms')
        seconds = milliseconds / 1000 if type(milliseconds) is int and milliseconds >= 0 else None
        return {'state': data['status'], 'provider_seconds': seconds, 'actual_charge': None,
                'error': (data.get('error') or {}).get('code', 'provider_failure'), 'outputs': data.get('outputs')}

    def outputs(self, status, output_nodes):
        outputs = status.get('outputs')
        if not isinstance(outputs, list) or len(outputs) > 64:
            raise ValueError('Invalid output collection')
        result = []
        media = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp'}
        for output in outputs:
            if output.get('node_id') not in output_nodes:
                continue
            if output.get('type') != 'image' or output.get('content_type') not in media:
                raise ValueError('v0 supports image outputs only')
            content = self.http.download(output['id'], output['content_type'])
            if len(content) != output.get('size_bytes'):
                raise ValueError('Output size mismatch')
            result.append((content, media[output['content_type']]))
        return result
