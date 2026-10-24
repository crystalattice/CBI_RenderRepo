# SPDX-License-Identifier: GPL-3.0-only
"""Comfy-local HTTP bridge to RR's existing CLI, with reviewed install sessions."""
import asyncio
import atexit
import hashlib
import io
import zipfile
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
import tempfile
import time
import re
import logging
from datetime import datetime, timezone
from urllib.parse import urlsplit, unquote
from types import SimpleNamespace
from yaml import YAMLError

from .archive import package_directory
from .cli import load_package
from .runtime import capture_runtime
from .installation_history import atomic_json, timestamp, lease, active, package_hash, within_scope, parse_plan

MAX_UPLOAD = 64 * 1024 * 1024
MAX_SESSIONS = 16
SESSION_SECONDS = 3600


class PackageUI:
    def __init__(self, root, artifact_dirs=(), python=None, input_dir=None):
        self.root = Path(root).resolve()
        self.python = str(python or sys.executable)
        self.input_dir = Path(input_dir).resolve() if input_dir else self.root / 'input'
        self.artifact_dirs = list(dict.fromkeys(str(Path(p).resolve()) for p in artifact_dirs if Path(p).is_dir()))
        self.target_url = None
        self.target_capabilities = {"install_nodes": True, "install_artifacts": True}
        self.preparation_lock = asyncio.Lock()
        self.sessions = {}
        self.repository_operations = {}
        self.busy = False
        self.backend_id = secrets.token_hex(16)
        self.store = self.root / '.renderrepo' / 'jobs'
        self.install_lock = self.root / '.renderrepo' / 'installation.lock'
        if (self.root / '.renderrepo').is_symlink() or self.store.is_symlink():
            raise ValueError('RR installation history must be a real installation-local directory.')
        self.temporary = tempfile.TemporaryDirectory(prefix='renderrepo-ui-')
        atexit.register(self.temporary.cleanup)
        self.history()

    def repository_args(self, **values):
        base = self.root / '.renderrepo' / 'repositories'
        return SimpleNamespace(sources=base / 'sources.yaml', cache_dir=base / 'cache',
                               timeout=30, repo=None, **values)

    def repositories(self):
        from .repository import sources, cache_status, cache_path, cached_packages, update_candidates
        from .receipts import read_receipts
        args = self.repository_args()
        warnings = []
        packages = cached_packages(args, report=warnings.append)
        installed = {r['name']: r for r in read_receipts(self.root)}
        for item in packages:
            receipt = installed.get(item['name'])
            item['installed_version'] = receipt['version'] if receipt else None
            item['installed_status'] = receipt['status'] if receipt else None
            item['installed_source'] = receipt.get('repository', {}).get('name') if receipt else None
            item['installed_identity'] = bool(receipt and receipt.get('repository', {}).get('name') == item['repository'] and receipt.get('repository', {}).get('index_uri') == item['index_uri'])
        installed_rows = []
        for receipt in installed.values():
            path = Path(receipt['workflow_path']) if receipt.get('workflow_path') else None
            available = bool(path and path.is_file())
            installed_rows.append({'name': receipt['name'], 'version': receipt['version'],
                'status': receipt['status'], 'source': receipt.get('repository', {}).get('name'),
                'workflow_path': str(path) if path else None, 'workflow_available': available,
                'workflow_modified': bool(available and hashlib.sha256(path.read_bytes()).hexdigest() != receipt['workflow_sha256'])})
        from .discovery import VOCABULARY, STOP_WORDS
        from .repository import official_catalog
        return {'official_catalog': official_catalog(args), 'search_vocabulary': VOCABULARY, 'search_stop_words': sorted(STOP_WORDS), 'installed': installed_rows,
                'sources': [dict(r, **cache_status(args, r), cached=cache_path(args, r).is_file())
                            for r in sources(args)], 'packages': packages,
                'updates': update_candidates(self.root, packages), 'warnings': warnings,
                'operations': [{k: v for k, v in operation.items() if k != 'task'}
                               for operation in list(self.repository_operations.values())]}

    def readiness(self, values, observed):
        from .readiness import evaluate
        from .receipts import read_receipts
        dependencies = None
        workflow_state = None
        capabilities = None
        if values.get('installed'):
            receipt = next((r for r in read_receipts(self.root) if r['name'] == values['installed']), None)
            if receipt is None:
                raise ValueError('Installed package not found.')
            dependencies = receipt.get('dependency_inventory')
            path = Path(receipt['workflow_path']) if receipt.get('workflow_path') else None
            if not path or not path.is_file():
                workflow_state = 'missing'
            else:
                data = path.read_bytes()
                if hashlib.sha256(data).hexdigest() != receipt['workflow_sha256']:
                    workflow_state = 'unknown'
                try:
                    from .capabilities import analyze
                    capabilities = analyze(json.loads(data), node_schema=observed.get('nodes'))
                except (ValueError, KeyError, TypeError, AttributeError):
                    pass
        else:
            item = next((p for p in self.repositories()['packages'] if
                         all(p.get(k) == values.get(k) for k in ('name', 'version', 'sha256')) and
                         p['repository'] == values.get('source')), None)
            if item is None:
                raise ValueError('Package listing changed; refresh and choose it again.')
            dependencies = item.get('dependency_inventory')
        report = evaluate(dependencies, observed, self.root, self.artifact_dirs, self.input_dir)
        if workflow_state:
            report['checks'].append({'state':workflow_state, 'kind':'installed workflow',
                                     'detail':'Workflow is missing.' if workflow_state == 'missing' else
                                              'Workflow changed; saved dependency inventory cannot qualify it.'})
            report['state'] = next(s for s in ('incompatible','missing','unknown') if s in {report['state'],workflow_state})
        if capabilities is not None:
            report['capabilities'] = capabilities
            report['installation_observations'] = self.installation_observations(data)
        report['dependency_source'] = 'installation receipt' if values.get('installed') else 'publisher catalog inventory (archive not retrieved)'
        return report

    def installation_observations(self, workflow_bytes):
        """Successful local install history, scoped to these exact workflow bytes.

        This records prior placement/hash verification, never an execution claim
        and never portable package metadata.
        """
        digest = hashlib.sha256(workflow_bytes).hexdigest()
        observations = []
        for directory in sorted(self.store.glob('*')):
            try:
                job = self.load_job(directory.name)
                scope = (job.get('plan') or {}).get('scope') or {}
                if (job.get('state') != 'done' or (job.get('result') or {}).get('exit_code') != 0
                        or scope.get('workflow', {}).get('sha256') != digest):
                    continue
                artifacts = scope.get('artifacts', [])
                observations.append({'session':directory.name, 'state':'installed',
                    'finished_at':job.get('attempts', [{}])[-1].get('finished_at') if job.get('attempts') else None,
                    'artifacts':[{'filename':a['filename'], 'operation':a['operation'],
                                  'package_sha256':a.get('sha256'),
                                  'hash_matched':bool(a.get('sha256') and a.get('reuse_sha256') == a['sha256'])}
                                 for a in artifacts],
                    'download_count':sum(a['operation']=='acquire' and any(not s.startswith('file:') for s in a.get('sources', [])) for a in artifacts),
                    'qualification':'Prior installation only; execution and hardware suitability untested.'})
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return observations

    def installed_workflow(self, name):
        from .receipts import read_receipts
        receipt = next((r for r in read_receipts(self.root) if r['name'] == name), None)
        if not receipt or not receipt.get('workflow_path'):
            raise ValueError('Installed workflow is unavailable; check Installed or installation details.')
        path = Path(receipt['workflow_path'])
        if not path.is_file():
            raise ValueError('Installed workflow file is missing. Review installation again to restore it.')
        data = path.read_bytes()
        graph = json.loads(data)
        if not isinstance(graph, dict) or not isinstance(graph.get('nodes'), list):
            raise ValueError('The installed file is not a Comfy editor graph. Preserve your edits and inspect the file before opening.')
        return data

    def repository_source(self, values):
        from .repository import change_source, repository_lock, refresh_repository, sources, configure_official
        if not isinstance(values, dict) or values.get('action') not in {'add', 'remove', 'enable', 'disable', 'official'}:
            raise ValueError('Choose a catalog operation and location.')
        if any(o['state'] == 'running' for o in self.repository_operations.values()):
            raise ValueError('Catalog operation active; wait before changing sources.')
        if values['action'] == 'official':
            if set(values) != {'action', 'uri'} or not isinstance(values['uri'], str):
                raise ValueError('Official catalog requires an index location, or blank to leave unconfigured.')
            args = self.repository_args()
            configure_official(args, values['uri'].strip())
            name = 'renderrepo-official'
        else:
            if set(values) - {'action', 'name', 'uri'} or not isinstance(values.get('name', ''), str):
                raise ValueError('Catalog name must be text.')
            name = values.get('name', '').strip()
            display_name = name
            if values['action'] == 'add':
                if len(name) > 100: raise ValueError('Use a display name of at most 100 characters.')
                if name: name = re.sub('[^A-Za-z0-9_-]+', '-', name).strip('-') or 'catalog'
                if not isinstance(values.get('uri'), str) or not values['uri'].strip():
                    raise ValueError('Enter a local catalog directory or HTTP(S) index URL.')
                if not name:
                    location = urlsplit(values['uri'])
                    stem = (location.hostname or Path(values['uri']).name or 'catalog')
                    name = re.sub('[^A-Za-z0-9_-]+', '-', stem).strip('-')[:48] or 'catalog'
                    existing = {r['name'] for r in sources(self.repository_args())}
                    base, n = name, 2
                    while name in existing:
                        name = f'{base}-{n}'; n += 1
            if not name:
                raise ValueError('Choose a catalog name.')
            args = self.repository_args(**dict(values, name=name), display_name=display_name)
            with repository_lock(args.sources.with_suffix('.yaml.lock')):
                change_source(args, report=lambda message: None)
        if values['action'] in {'add', 'official'}:
            source = next((r for r in sources(args) if r['name'] == name), None)
            if source:
                refresh_repository(args, source, report=lambda message: None)
        return dict(self.repositories(), changed_source=name)

    def repository_operation(self, token):
        if token not in self.repository_operations:
            raise ValueError('Repository operation unavailable; refresh sources or retrieve the archive again explicitly.')
        return {k: v for k, v in self.repository_operations[token].items() if k != 'task'}

    def new_repository_operation(self, kind):
        if any(o['state'] == 'running' for o in self.repository_operations.values()):
            raise ValueError('Repository operation active; wait for its progress to finish.')
        for token in list(self.repository_operations):
            if len(self.repository_operations) >= 16:
                del self.repository_operations[token]
        token = secrets.token_urlsafe(32)
        operation = {'token': token, 'kind': kind, 'state': 'running', 'progress': 'Starting…',
                     'started_at': timestamp(), 'sources': [], 'result': None, 'error': None}
        self.repository_operations[token] = operation
        return token, operation

    async def repository_refresh(self, values):
        from .repository import selected_sources, refresh_repository
        if not isinstance(values, dict) or set(values) != {'source'} or (values['source'] is not None and not isinstance(values['source'], str)):
            raise ValueError('Refresh requires a source name or null for all enabled sources.')
        args = self.repository_args()
        args.repo = values['source']
        configured = selected_sources(args)
        token, operation = self.new_repository_operation('refresh')
        async def run():
            try:
                for source in configured:
                    if not source['enabled']:
                        continue
                    result = {'name': source['name'], 'state': 'running', 'log': ''}
                    operation['sources'].append(result)
                    operation['progress'] = 'Refreshing ' + source['name'] + '… (indexes only)'
                    messages = []
                    try:
                        ok = await asyncio.to_thread(refresh_repository, args, source, messages.append)
                        result['state'] = 'success' if ok else 'failed'
                    except (ValueError, OSError) as error:
                        result['state'] = 'failed'
                        messages.append(str(error))
                    result['log'] = '\n'.join(messages)
                succeeded = sum(s['state'] == 'success' for s in operation['sources'])
                failed = sum(s['state'] == 'failed' for s in operation['sources'])
                operation.update(state='done', progress=f'Refresh finished: {succeeded} succeeded, {failed} failed.')
            except Exception as error:
                operation.update(state='failed', error=str(error))
            finally:
                operation['finished_at'] = timestamp()
        operation['task'] = asyncio.create_task(run())
        return self.repository_operation(token)

    async def repository_retrieve(self, values):
        from .repository import select_package, acquire_package
        if (not isinstance(values, dict) or set(values) - {'name', 'version', 'source', 'sha256', 'approved'}
                or ('approved' in values and values['approved'] is not True)
                or not all(isinstance(values.get(k), str) and values[k] for k in ('name', 'version', 'source', 'sha256'))):
            raise ValueError('Select an explicit package, catalog, and version for inspection.')
        args = self.repository_args(package=Path(values['name']), version=values['version'], yes=True)
        args.repo = values['source']
        selected = await asyncio.to_thread(select_package, args, lambda message: None)
        if selected['sha256'] != values['sha256']:
            raise ValueError('Catalog digest changed; search and select the package again.')
        if selected['size_bytes'] > MAX_UPLOAD:
            raise ValueError('Selected archive exceeds the extension’s 64 MiB import limit; use the CLI.')
        args.expected_catalog = selected
        token, operation = self.new_repository_operation('archive')
        operation['progress'] = f"Retrieving {selected['name']} {selected['version']} [{selected['repository']}] — {selected['size_bytes']} bytes; SHA-256 verification required. No artifacts installed."
        async def run():
            try:
                path = await asyncio.to_thread(acquire_package, args, lambda message: None)
                data = await asyncio.to_thread(path.read_bytes)
                operation['progress'] = 'Archive verified. Building the existing installation review…'
                result = await self.preview(data, {}, repository_info=args.repository_info,
                                            expected_package=args.expected_package)
                operation.update(state='done', result=result, progress='Archive verified; review and approve installation separately.')
            except Exception as error:
                operation.update(state='failed', error=str(error), progress='Archive retrieval/review failed. No automatic retry.')
            finally:
                operation['finished_at'] = timestamp()
        operation['task'] = asyncio.create_task(run())
        return self.repository_operation(token)

    async def retrieve_link(self, values):
        if not isinstance(values, dict) or set(values) != {'url'} or not isinstance(values['url'], str):
            raise ValueError('Enter a direct package URL.')
        from .package_link import retrieve_link
        token, operation = self.new_repository_operation('archive')
        operation['progress'] = 'Downloading package for inspection. Dependencies will not be installed.'
        async def run():
            try:
                path = await asyncio.to_thread(retrieve_link, values['url'], self.repository_args().cache_dir)
                result = await self.preview(await asyncio.to_thread(path.read_bytes), {})
                session = self.get(result['session'])
                session['direct_source'] = values['url']
                self.persist(session)
                result['source'] = values['url']
                operation.update(state='done', result=result, progress='Package ready for installation review.')
            except Exception as error:
                operation.update(state='failed', error=str(error), progress='Package inspection failed. Correct the link or import a local .rrpkg and retry.')
            finally:
                operation['finished_at'] = timestamp()
        operation['task'] = asyncio.create_task(run())
        return self.repository_operation(token)

    def persist(self, session):
        if not session.get('durable'):
            return
        record = {key: value for key, value in session.items()
                  if key not in {'directory', 'package', 'control_file', 'task', 'process'} }
        record.update(job_version=1, comfy_root=str(self.root), updated_at=timestamp())
        session['updated_at'] = record['updated_at']
        atomic_json(session['directory'] / 'job.json', record)

    def load_job(self, token):
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
            raise ValueError('Invalid installation identity.')
        directory = self.store / token
        if directory.is_symlink() or directory.resolve().parent != self.store.resolve():
            raise ValueError('Invalid retained installation directory.')
        record = json.loads((directory / 'job.json').read_text())
        if (not isinstance(record, dict) or record.get('job_version') != 1
                or record.get('comfy_root') != str(self.root)
                or record.get('state') not in {'reviewed', 'running', 'done', 'interrupted', 'needs_review'}
                or not isinstance(record.get('options'), dict)
                or not isinstance(record.get('attempts'), list)
                or not all(isinstance(attempt, dict) for attempt in record['attempts'])
                or not isinstance(record.get('plan'), dict)
                or not isinstance(record['plan'].get('log'), str)
                or type(record['plan'].get('exit_code')) is not int
                or not isinstance(record.get('argv'), list)
                or not all(isinstance(arg, str) for arg in record['argv'])
                or not isinstance(record.get('package_sha256'), str)
                or (record.get('result') is not None and (not isinstance(record['result'], dict)
                    or not isinstance(record['result'].get('log'), str)
                    or type(record['result'].get('exit_code')) is not int))):
            raise ValueError('Invalid installation history record.')
        for field in ('created_at', 'updated_at'):
            if not isinstance(record.get(field), str):
                raise ValueError('Invalid installation activity timestamp.')
            datetime.fromisoformat(record[field])
        for scope in (record['plan'].get('scope'), record.get('approved_scope'), record.get('destination_scope')):
            if scope is not None and (not isinstance(scope, dict) or not isinstance(scope.get('workflow'), dict)):
                raise ValueError('Invalid retained installation scope.')
        self.options(record['options'])
        if record.get('repository_info'):
            from .repository import validate_provenance
            provenance = validate_provenance(record['repository_info'])
            if (json.loads((directory / 'repository.json').read_text()) != provenance
                    or provenance['archive_sha256'] != record.get('archive_sha256')):
                raise ValueError('Retained repository provenance changed; retrieve and review a clean archive.')
        if (directory / 'package').is_symlink():
            raise ValueError('Retained package must be a real directory.')
        manifest = load_package(directory / 'package')
        if manifest != record.get('manifest'):
            raise ValueError('Retained package manifest changed; remove history and import a clean package.')
        record.update(directory=directory, package=directory / 'package',
                      control_file=directory / 'download-control.json', durable=True)
        if record['control_file'].exists():
            record['control'] = json.loads(record['control_file'].read_text())
        old = self.sessions.get(token, {})
        for key in ('task', 'process'):
            if key in old:
                record[key] = old[key]
        self.sessions[token] = record
        if record['state'] == 'running' and not active(directory / 'worker.lock'):
            # The worker may publish completion between the read and lease probe.
            # Re-read while holding its lease before marking an interruption.
            try:
                worker_lock = lease(directory / 'worker.lock')
            except ValueError:
                return self.load_job(token)
            with worker_lock:
                fresh = json.loads((directory / 'job.json').read_text())
                if fresh['state'] != 'running':
                    return self.load_job(token)
                record.update(state='interrupted', outcome='interrupted',
                              result={'exit_code': 2, 'log': 'Installation interrupted. Resume rebuilds the plan; nothing restarts automatically.'})
                self.persist(record)
        return record

    def history(self):
        entries = []
        for directory in sorted(self.store.glob('*')):
            if not directory.is_dir() and not directory.is_symlink():
                continue
            token = directory.name
            try:
                record = self.load_job(token)
                status = self.status(token)
                entries.append({'session': token, 'name': record['manifest']['name'],
                                'version': record['manifest'].get('version', '0'),
                                **{key: status[key] for key in ('state', 'outcome', 'worker_active', 'transfer', 'control', 'last_activity',
                                                               'workflow_available', 'workflow_path')},
                                'failure': next((line for line in reversed((status.get('result') or {}).get('log', '').splitlines())
                                    if line.startswith(('FAILED SOURCE:', 'ERROR:'))), None)
                                    if status.get('outcome') in {'failed', 'cancelled', 'interrupted'} else None})
            except (ValueError, OSError, KeyError, TypeError) as error:
                entries.append({'session': token, 'state': 'corrupt', 'name': 'Unreadable installation',
                                'error': str(error), 'version': '', 'last_activity': None})
        return sorted(entries, key=lambda item: item.get('last_activity') or '', reverse=True)

    def node_inventory(self):
        nodes = capture_runtime(self.root)['custom_nodes']
        for node in nodes:
            reasons = []
            if not node.get('revision'):
                reasons.append('missing Git history or unreadable commit')
            else:
                if node.get('dirty') is True:
                    reasons.append('local changes (including untracked files)')
                elif node.get('dirty') is not False:
                    reasons.append('Git working-tree status unavailable')
            if not node.get('source'):
                reasons.append('missing or unsupported origin URL')
            node['exportable'] = not reasons
            node['unavailable_reason'] = '; '.join(reasons)
        return nodes

    def cleanup(self):
        now = time.monotonic()
        for token, session in list(self.sessions.items()):
            if not session.get('durable') and session['state'] != 'running' and now - session['created'] > SESSION_SECONDS:
                self.discard(token)

    def search_local_files(self, values):
        if not isinstance(values, dict) or set(values) != {'query', 'directories'}:
            raise ValueError('Expected a filename search and directories.')
        query = values['query']
        directories = values['directories']
        if not isinstance(query, str) or len(query.strip()) < 2 or len(query) > 200:
            raise ValueError('Enter at least two characters of the filename.')
        if not isinstance(directories, list) or not all(isinstance(p, str) for p in directories):
            raise ValueError('Search directories must be paths.')
        roots = [Path(p).expanduser().resolve() for p in [*self.artifact_dirs, str(self.root / 'models'), *directories]]
        if directories and any(not Path(p).expanduser().is_dir() for p in directories):
            raise ValueError('One of the additional search directories does not exist.')
        matches, seen, scanned = [], set(), 0
        for root in dict.fromkeys(roots):
            if not root.is_dir():
                continue
            for folder, folders, files in os.walk(root, followlinks=False):
                folders[:] = sorted(p for p in folders if not p.startswith('.'))
                for name in sorted(files):
                    scanned += 1
                    if query.casefold() in name.casefold():
                        path = Path(folder) / name
                        if path.is_file() and str(path) not in seen:
                            seen.add(str(path)); matches.append({'name': name, 'path': str(path)})
                    if scanned >= 50000 or len(matches) >= 100:
                        return {'files': matches, 'truncated': True}
        return {'files': matches, 'truncated': False}

    def new_session(self, durable=False):
        self.cleanup()
        if sum(s['state'] in {'new', 'reviewed'} for s in self.sessions.values()) >= MAX_SESSIONS:
            raise ValueError('Too many pending packages; close old package dialogs first.')
        token = secrets.token_urlsafe(32)
        directory = (self.store if durable else Path(self.temporary.name)) / token
        directory.mkdir(parents=True, mode=0o700)
        session = {'directory': directory, 'created': time.monotonic(), 'created_at': timestamp(),
                   'state': 'new', 'result': None, 'durable': durable, 'attempts': []}
        self.sessions[token] = session
        return token, session

    def get(self, token):
        self.cleanup()
        if token in self.sessions and self.sessions[token]['state'] == 'new':
            return self.sessions[token]
        if (self.store / token).exists():
            return self.load_job(token)
        if token not in self.sessions:
            raise ValueError('Package job is missing or was removed.')
        return self.sessions[token]

    def discard(self, token):
        if (self.store / token).exists():
            if not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
                raise ValueError('Invalid installation identity.')
            directory = self.store / token
            if directory.is_symlink():
                directory.unlink()
            else:
                with lease(self.install_lock), lease(directory / 'worker.lock'):
                    shutil.rmtree(directory)
            self.sessions.pop(token, None)
            return
        session = self.sessions.get(token)
        if session and session['state'] == 'running':
            raise ValueError('Installation is running; wait for its result before closing.')
        if session:
            shutil.rmtree(session['directory'])
            del self.sessions[token]

    async def command(self, argv, progress=None):
        process = await asyncio.create_subprocess_exec(
            self.python, '-u', '-m', 'renderrepo', *map(str, argv),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            async def collect():
                output = bytearray()
                while chunk := await process.stdout.read(4096):
                    output.extend(chunk)
                    if progress:
                        progress(chunk.decode('utf-8', errors='replace'))
                    if len(output) > 256000:
                        del output[:-256001]
                await process.wait()
                return bytes(output)
            output = await asyncio.wait_for(collect(), timeout=None if progress else 3600)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            process.terminate()
            await process.wait()
            raise
        return {'exit_code': process.returncode, 'log': output.decode('utf-8', errors='replace')}

    def options(self, values):
        if not isinstance(values, dict) or set(values) - {'artifact_dirs', 'sources', 'python_dependencies'}:
            raise ValueError('Invalid installation options.')
        dirs = values.get('artifact_dirs', [])
        sources = values.get('sources', [])
        if not isinstance(dirs, list) or not all(isinstance(x, str) and x for x in dirs):
            raise ValueError('Artifact directories must be a list of server paths.')
        if not isinstance(sources, list) or not all(isinstance(x, str) and '=' in x for x in sources):
            raise ValueError('Sources must be FILENAME=URL or FILENAME=PATH lines.')
        if not isinstance(values.get('python_dependencies', False), bool):
            raise ValueError('Python dependency choice must be boolean.')
        argv = []
        for directory in dict.fromkeys([*self.artifact_dirs, *dirs]):
            argv += ['--artifact-dir', directory]
        for source in sources:
            argv += ['--source', source]
        if values.get('python_dependencies'):
            if os.path.abspath(self.python) != os.path.abspath(sys.executable):
                raise ValueError('Python changes must use the selected Comfy server interpreter: ' + sys.executable + '. Relaunch that server with the intended environment; RR will not change another interpreter.')
            argv += ['--comfy-python', self.python]
        return argv

    async def preview(self, data, options, repository_info=None, expected_package=None, *, _review_locked=False, node_mappings=None):
        if not isinstance(data, bytes) or len(data) > MAX_UPLOAD:
            raise ValueError('RR packages must be at most 64 MiB in the browser importer.')
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(item.file_size for item in archive.infolist()) > MAX_UPLOAD:
                raise ValueError('Browser packages must also unpack to at most 64 MiB.')
        token, session = self.new_session(durable=True)
        try:
            archive = session['directory'] / 'upload.rrpkg'
            archive.write_bytes(data)
            package = session['directory'] / 'package'
            def extract():
                with package_directory(archive) as extracted:
                    shutil.copytree(extracted, package)
                manifest = load_package(package)
                graph = json.loads((package / manifest['workflow']).read_bytes())
                if not isinstance(graph.get('nodes'), list):
                    raise ValueError('Comfy editor import requires editor-format JSON; API prompts belong in the SDK runner.')
                # Publish a job only after its retained import is durable too.
                for path in session['directory'].rglob('*'):
                    if path.is_file():
                        with path.open('rb') as stream:
                            os.fsync(stream.fileno())
                for directory in [*sorted((p for p in session['directory'].rglob('*') if p.is_dir()), reverse=True), session['directory'], self.store]:
                    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                return manifest
            manifest = await asyncio.to_thread(extract)
            if expected_package and (manifest['name'] != expected_package['name'] or manifest.get('version', '0') != expected_package['version']):
                raise ValueError('Archive manifest identity does not match the repository index.')
            if repository_info:
                from .repository import validate_provenance
                repository_info = validate_provenance(repository_info)
                if repository_info['archive_sha256'] != hashlib.sha256(data).hexdigest():
                    raise ValueError('Repository archive digest does not match retained bytes.')
                session['repository_info'] = repository_info
                atomic_json(session['directory'] / 'repository.json', repository_info)
            session.update(package=package, manifest=manifest, archive_sha256=hashlib.sha256(data).hexdigest())
            session['package_sha256'] = package_hash(package)
            if node_mappings is not None:
                session['user_node_mappings'] = node_mappings
            return await (self._review(token, options) if _review_locked else self.review(token, options))
        except BaseException:
            self.discard(token)
            raise

    async def target_check(self, session):
        """Read this bridge's selected environment; never a publisher-selected host."""
        if self.target_url is None:
            return None
        from .readiness import snapshot, inventory, evaluate
        from .runtime import graph_nodes
        observed = await asyncio.to_thread(snapshot, self.target_url, 5)
        data = (session['package'] / session['manifest']['workflow']).read_bytes()
        graph = json.loads(data)
        from .capabilities import resolve_summary
        session['capabilities'] = resolve_summary(graph, session['manifest'].get('capabilities'), node_schema=observed.get('nodes'))
        report = await asyncio.to_thread(evaluate, inventory(session['manifest'], graph), observed,
                                         self.root, self.artifact_dirs, self.input_dir)
        available = observed.get('nodes')
        declared = {n['name']: n for n in session['manifest'].get('runtime', {}).get('custom_nodes', [])}
        types = {n['type']: n for n in graph_nodes(graph) if n['type'] not in {'Note','MarkdownNote','Reroute'}}
        requirements = []
        for typ, node in types.items():
            package = (node.get('properties') or {}).get('cnr_id')
            source = (session.get('user_node_mappings') or {}).get(typ) or declared.get(package)
            state = ('unknown' if available is None else 'available' if typ in available else
                     'resolvable' if source and self.target_capabilities['install_nodes'] else
                     'needs_source' if self.target_capabilities['install_nodes'] else 'unavailable')
            requirements.append({'type': typ, 'state': state, 'repository': source})
        incompatible = [c for c in report['checks'] if c['kind']=='runtime requirement' and c['state']=='incompatible']
        blockers = [r['type'] for r in requirements if r['state'] in {'unavailable','needs_source'}]
        state = ('unavailable' if blockers or incompatible else 'unknown' if any(r['state']=='unknown' for r in requirements) or any(c['state']=='unknown' and c['kind']=='runtime requirement' for c in report['checks'])
                 else 'resolvable' if any(r['state']=='resolvable' for r in requirements) else 'available')
        return {'state': state, 'nodes': requirements, 'blockers': blockers,
                'runtime_blockers': incompatible, 'server': self.target_url,
                'capabilities': self.target_capabilities, 'evidence': report,
                'installation_observations': self.installation_observations(data),
                'limitation': 'Static requirements only; execution, memory fit and speed are not guaranteed.'}

    async def preview_workflow(self, data, filename='workflow.json'):
        """Ordinary editor JSON enters the same durable package review/worker path."""
        if not isinstance(data, bytes) or len(data)>MAX_UPLOAD:
            raise ValueError('Workflow JSON must be at most 64 MiB.')
        graph=json.loads(data)
        if not isinstance(graph,dict) or not isinstance(graph.get('nodes'),list) or not graph['nodes']:
            raise ValueError('API-format prompts are not editor workflows. Import an ordinary Comfy editor workflow JSON; API graph opening/bindings are not reliably resolved here.')
        from .pack import discover
        from .archive import write_archive
        artifacts, inputs, runtime, unresolved=discover(graph)
        # Catalog/receipt identities are evidence; ambiguous same-name artifacts stay unresolved.
        from .receipts import read_receipts
        known=[]
        for item in self.repositories()['packages']:
            known.extend((item.get('dependency_inventory') or {}).get('artifacts',[]))
        for receipt in read_receipts(self.root):
            known.extend((receipt.get('dependency_inventory') or {}).get('artifacts',[]))
        mapping_file=self.root/'.renderrepo/user-mappings.json'
        if mapping_file.is_symlink(): raise ValueError('User mappings must be installation-local regular files.')
        mappings=json.loads(mapping_file.read_text()) if mapping_file.is_file() else {'artifacts':{},'nodes':{}}
        mapping_evidence=[]
        for artifact in artifacts:
            matches=[a for a in known if a['filename']==artifact['filename'] and a.get('sha256')]
            identities={a['sha256'] for a in matches}
            if len(identities)==1:
                chosen=matches[0]
                mapping_evidence.append({'filename':artifact['filename'],'basis':'catalog filename mapping; artifact hash verifies catalog content, not workflow/model compatibility','sha256':chosen['sha256']})
                for key in ('sha256','size_bytes','sources'):
                    if key in chosen: artifact[key]=chosen[key]
            elif len(identities)>1:
                artifact['sources']=[]
            elif artifact['filename'] in mappings.get('artifacts',{}):
                saved=mappings['artifacts'][artifact['filename']]
                artifact['sources']=[{'uri':saved['source']}]
                # User mappings are explicitly unverified, not official identity metadata.
        runtime['custom_nodes']=[]
        candidates={}
        for item in self.repositories()['packages']:
            for requirement in (item.get('dependency_inventory') or {}).get('runtime',{}).get('custom_nodes',[]):
                choices=candidates.setdefault(requirement['name'],[])
                if requirement not in choices: choices.append(requirement)
        for node in self.node_inventory():
            if node.get('exportable') and node.get('source') and node.get('revision'):
                requirement={k:node[k] for k in ('name','source','revision')}
                choices=candidates.setdefault(node['name'],[])
                if requirement not in choices: choices.append(requirement)
        # A package id is not a version selection. Conflicting pins need an explicit mapping.
        known_nodes={name:choices[0] for name,choices in candidates.items() if len(choices)==1}
        from .runtime import graph_nodes
        for node in graph_nodes(graph):
            typ=node['type']
            explicit=(node.get('properties') or {}).get('cnr_id')
            if explicit in known_nodes and known_nodes[explicit] not in runtime['custom_nodes']: runtime['custom_nodes'].append(known_nodes[explicit])
            if typ in mappings.get('nodes',{}):
                req=mappings['nodes'][typ]
                node.setdefault('properties',{})['cnr_id']=req['name']
                if req not in runtime['custom_nodes']: runtime['custom_nodes'].append(req)
        # Never rewrite original JSON to attach mapping metadata.
        name=re.sub(r'[^A-Za-z0-9_.-]','-',Path(filename).stem).strip('.') or 'imported-workflow'
        manifest={'schema_version':0,'name':name,'version':'0.1.0','workflow':'workflow.json',
                  'artifacts':artifacts,'inputs':inputs,'runtime':runtime,'unresolved':unresolved}
        with tempfile.TemporaryDirectory(prefix='rr-workflow-import-') as temporary:
            folder=Path(temporary)/'package';folder.mkdir()
            (folder/'workflow.json').write_bytes(data)
            (folder/'manifest.yaml').write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n')
            from .capsule import seal
            seal(folder,manifest)
            archive=Path(temporary)/'workflow.rrpkg';write_archive(folder,archive)
            result=await self.preview(archive.read_bytes(),{})
        session=self.get(result['session'])
        session['imported_workflow']={'filename':filename,'sha256':hashlib.sha256(data).hexdigest(),
                                      'mapping_provenance':'user selections are not official verified metadata','catalog_mappings':mapping_evidence}
        (session['directory']/'original-workflow.json').write_bytes(data)
        session['user_node_mappings']=mappings.get('nodes',{})
        self.persist(session)
        return await self.review(result['session'],{})

    async def resolve_requirement(self, token, values):
        async with self.preparation_lock:
            return await self._resolve_requirement(token, values)

    async def _resolve_requirement(self, token, values):
        session=self.get(token)
        if session['state']=='running': raise ValueError('Wait for installation to stop before changing its scope.')
        if not isinstance(values,dict) or set(values)-{'kind','name','source','revision','repository'}:
            raise ValueError('Choose only the unresolved requirement and its source.')
        name=values.get('name');source=values.get('source')
        if not isinstance(name,str): raise ValueError('A requirement name is required.')
        if not isinstance(source,str) or not source: raise ValueError('A source URL or server-side path is required.')
        mapping_file=self.root/'.renderrepo/user-mappings.json'
        if mapping_file.is_symlink(): raise ValueError('User mappings must be installation-local regular files.')
        mappings=json.loads(mapping_file.read_text()) if mapping_file.is_file() else {'artifacts':{},'nodes':{}}
        if values.get('kind')=='artifact':
            artifact=next((a for a in session['manifest']['artifacts'] if a['filename']==name),None)
            if artifact is None: raise ValueError('Unknown artifact requirement.')
            from .download import source_uri
            normalized=source_uri(source)
            options=dict(session['options'])
            options['sources']=[s for s in options.get('sources',[]) if s.partition('=')[0]!=name]+[name+'='+source]
            mappings['artifacts'][name]={'source':normalized,'provenance':'user-selected; not official verified metadata'}
            atomic_json(mapping_file,mappings)
            return await self._review(token,options)
        if values.get('kind')!='node': raise ValueError('Unknown requirement kind.')
        from .nodes import validate_nodes
        from .runtime import graph_nodes
        graph=json.loads((session['package']/session['manifest']['workflow']).read_bytes())
        if name not in {n['type'] for n in graph_nodes(graph)}: raise ValueError('Unknown node requirement.')
        req={'name':values.get('repository'),'source':source,'revision':values.get('revision')}
        validate_nodes([req])
        # A node pin changes portable package declarations. Never rewrite the
        # imported capsule: retain it and review a new, predecessor-linked copy.
        import copy
        manifest=copy.deepcopy(session['manifest'])
        entries=manifest.setdefault('runtime',{}).setdefault('custom_nodes',[])
        if any(n['name']==req['name'] and n!=req for n in entries):
            raise ValueError('Conflicting node repository requirement.')
        if req not in entries: entries.append(req)
        selected={**session.get('user_node_mappings',{}),name:req}
        from .capsule import copy_capsule
        def edit(folder):
            (folder/'manifest.yaml').write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n')
        with tempfile.TemporaryDirectory(prefix='rr-capsule-revision-') as temporary:
            archive=Path(temporary)/'revision.rrpkg'
            from .capsule import parent_descriptor
            parent=parent_descriptor(session['package'])
            provenance=copy.deepcopy(parent['provenance'])
            source={k:copy.deepcopy(session[k]) for k in ('repository_info','direct_source','imported_workflow') if session.get(k)}
            if source: provenance.setdefault('source_records',[]).append({'capsule_sha256':parent['sha256'],**source})
            copy_capsule(session['package'],archive,edit=edit,provenance=provenance)
            result=await self.preview(archive.read_bytes(),session['options'],
                                      _review_locked=True,node_mappings=selected)
        derivative=self.get(result['session'])
        for field in ('repository_info','direct_source','imported_workflow'):
            if session.get(field):
                derivative.setdefault('predecessor_provenance',{})[field]=copy.deepcopy(session[field])
        if session.get('imported_workflow'):
            derivative['imported_workflow']=copy.deepcopy(session['imported_workflow'])
            original=session['directory']/'original-workflow.json'
            if original.is_file(): shutil.copyfile(original,derivative['directory']/'original-workflow.json')
        self.persist(derivative)
        mappings['nodes'][name]=req
        atomic_json(mapping_file,mappings)
        return {**result,'imported_workflow':derivative.get('imported_workflow')}

    async def review(self, token, options):
        async with self.preparation_lock:
            return await self._review(token, options)

    async def _review(self, token, options):
        session = self.get(token)
        if session['state'] == 'running':
            raise ValueError('This installation worker is still running.')
        if package_hash(session['package']) != session['package_sha256']:
            raise ValueError('Retained package changed; import a clean package.')
        self.options(options)
        # A source selector represents one destination, not an invisible fallback
        # chain. Preserve explicit choices; use the first publisher source by default.
        options = {**options, 'sources': list(options.get('sources', []))}
        selected = {s.partition('=')[0] for s in options['sources']}
        for artifact in session['manifest']['artifacts']:
            if len(artifact.get('sources', [])) > 1 and artifact['filename'] not in selected:
                options['sources'].append(artifact['filename'] + '=' + artifact['sources'][0]['uri'])
        argv = ['install', str(session['package']), '--comfy-dir', str(self.root), '--input-dir', str(self.input_dir)]
        if session['manifest'].get('runtime', {}).get('custom_nodes'):
            argv += ['--install-nodes']
        if session.get('repository_info'):
            argv += ['--repository-info', str(session['directory'] / 'repository.json')]
        argv += self.options(options)
        plan = parse_plan(await self.command([*argv, '--dry-run', '--plan-json']))
        from .capabilities import resolve_summary
        graph = json.loads((session['package'] / session['manifest']['workflow']).read_bytes())
        session['capabilities'] = resolve_summary(graph, session['manifest'].get('capabilities'))
        target=await self.target_check(session)
        if target and not self.target_capabilities['install_artifacts']:
            blocked = [a['filename'] for a in (plan.get('scope') or {}).get('artifacts', [])
                       if a['operation'] != 'reuse']
            if blocked:
                target['blockers'].extend(blocked)
                target['state'] = 'unavailable'
        if target and target['state']=='unavailable':
            plan['exit_code']=2
            plan['log']+='\nUNAVAILABLE ON SELECTED TARGET: '+', '.join(target['blockers'])
        session.update(argv=argv, plan=plan, options=options, state='reviewed', target_check=target)
        if plan['scope']:
            session['destination_scope'] = plan['scope']
        self.persist(session)
        return {**self.store_scope(session), 'capabilities': session.get('capabilities'), 'review_id':self.review_identity(session), 'session': token, 'name': session['manifest']['name'], 'version': session['manifest'].get('version', '0'),
                'imported_workflow':session.get('imported_workflow'),'manifest': session['manifest'], 'capsule': self.capsule_identity(session), 'archive_sha256': session['archive_sha256'],
                'source': session.get('repository_info', {}).get('name') or session.get('direct_source') or 'Imported file',
                'plan': plan, 'target_check': target, 'options':options, 'can_install': plan['exit_code'] == 0 and bool(plan['scope'])}

    def capsule_identity(self, session):
        if 'package' not in session:
            return None  # Export status is an operation, not an imported-package review.
        from .capsule import verify, identity
        descriptor=verify(session['package'],session['manifest'])
        return identity(descriptor) if descriptor else None

    def store_scope(self, session):
        """Plain scope and focused decisions derived from the very same approved plan."""
        scope = session.get('plan', {}).get('scope') or {}
        artifacts = scope.get('artifacts', [])
        downloads = [a for a in artifacts if a.get('operation') == 'acquire'
                     and any(not uri.startswith('file:') for uri in a.get('sources', []))]
        copies = [a for a in artifacts if a.get('operation') == 'acquire' and a not in downloads]
        reused = [a for a in artifacts if a.get('operation') == 'reuse']
        known = sum(a.get('size_bytes') or 0 for a in downloads)
        unknown = [a['filename'] for a in downloads if a.get('size_bytes') is None]
        decisions = []
        code = [n for n in scope.get('nodes', []) if n.get('operation', 'install') != 'reuse']
        if code:
            decisions.append({'id': 'code', 'message': 'Install or check custom-node code: ' +
                ', '.join(n['name'] for n in code) + '. This code runs in Comfy.'})
        if scope.get('python'):
            decisions.append({'id': 'python', 'message': 'Install the selected Python requirements in the Comfy environment. Shared Python packages may change.'})
        if unknown:
            decisions.append({'id': 'unknown-size', 'message': 'Download files whose size is unknown: ' + ', '.join(unknown) + '.'})
        summary = 'Add one workflow'
        if copies:
            summary += ' and ' + ('one example image' if len(copies) == 1 and any(
                i.get('mode') == 'bundled' and i.get('kind') == 'image' and i.get('filename') == copies[0]['filename']
                for i in session.get('manifest', {}).get('inputs', [])) else str(len(copies)) + ' bundled/local file(s)')
        summary += '.'
        if not downloads:
            summary += ' No file downloads.' if code or scope.get('python') else ' No downloads.'
        if code:
            summary += ' Install ' + str(len(code)) + ' custom-node code package(s); confirmation required.'
        if scope.get('python'):
            summary += ' Change Python packages in ' + self.python + '; confirmation required.'
        if reused:
            summary += ' Reuse ' + str(len(reused)) + ' existing file' + ('s.' if len(reused) != 1 else '.')
        return {'store_scope': {'summary': summary, 'downloads': len(downloads),
                'download_bytes': known, 'unknown_sizes': len(unknown), 'reused': len(reused),
                'decisions': decisions}}

    async def resume(self, token):
        session = self.get(token)
        if session['state'] == 'running':
            raise ValueError('This installation worker is still running.')
        approved_target = session.get('approved_target')
        approved = session.get('approved_scope')
        review = await self.review(token, session['options'])
        if review['can_install'] and within_scope(approved, review['plan']['scope']) and approved_target == self.target_identity(review.get('target_check')):
            return await self.approve(token, True, preserve_approval=True)
        session = self.get(token)
        session.update(state='needs_review', result={'exit_code': 2,
            'log': 'Installation plan changed. Review the revised plan and approve before resuming.'})
        self.persist(session)
        return {**review, 'state': 'needs_review', 'approval_required': True}

    def review_identity(self, session):
        value={'scope':session.get('plan',{}).get('scope'),'options':session.get('options'),'target':self.target_identity(session.get('target_check'))}
        return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()

    @staticmethod
    def target_identity(check):
        return {k:check[k] for k in ('state','nodes','blockers','runtime_blockers','server','capabilities')} if check else None

    async def approve(self, token, approved, *, preserve_approval=False, review_id=None, confirmations=None):
        # Serialize option changes and authorization with the existing preparation lock.
        async with self.preparation_lock:
            return await self._approve(token, approved, preserve_approval=preserve_approval,
                                       review_id=review_id, confirmations=confirmations)

    async def _approve(self, token, approved, *, preserve_approval=False, review_id=None, confirmations=None):
        if approved is not True:
            raise ValueError('Explicit approval is required.')
        session = self.get(token)
        if review_id is not None and review_id != self.review_identity(session):
            return {**(await self._review(token,session['options'])), 'approval_required':True}
        if confirmations is not None:
            if not isinstance(confirmations, list) or any(not isinstance(c, str) for c in confirmations):
                raise ValueError('Invalid installation confirmations.')
            required = {d['id'] for d in self.store_scope(session)['store_scope']['decisions']}
            if required != set(confirmations):
                return {**(await self._review(token, session['options'])), 'confirmation_required': True}
        if session['state'] not in {'reviewed', 'needs_review'} or session['plan']['exit_code'] != 0:
            raise ValueError('Review a satisfiable plan before approving installation.')
        if self.busy:
            raise ValueError('Another RR installation is running; wait for it to finish.')
        if package_hash(session['package']) != session['package_sha256']:
            raise ValueError('Retained package changed; import a clean package.')
        target=await self.target_check(session)
        previous=session.get('target_check')
        if self.target_identity(target)!=self.target_identity(previous) or target and target['state']=='unavailable':
            return {**(await self._review(token,session['options'])), 'approval_required':True}
        with lease(self.install_lock) as global_lock, lease(session['directory'] / 'worker.lock') as job_lock:
            # Rebuild under the worker lease, immediately before dispatch.
            current=self.get(token)
            if self.review_identity(current)!=self.review_identity(session):
                return {**(await self._review(token,current['options'])), 'approval_required':True}
            session=current
            fresh = parse_plan(await self.command([*session['argv'], '--dry-run', '--plan-json']))
            scope = session.get('approved_scope') if preserve_approval else session['plan']['scope']
            fresh_decisions = {d['id'] for d in self.store_scope({**session, 'plan':fresh})['store_scope']['decisions']}
            additional_decisions = confirmations is not None and not fresh_decisions <= set(confirmations)
            if fresh['exit_code'] or not within_scope(scope, fresh['scope']) or additional_decisions:
                session.update(state='needs_review', plan=fresh,
                    result={'exit_code': 2, 'log': 'Installation plan changed. Review the revised plan before approval.'})
                self.persist(session)
                return {**self.store_scope(session),'review_id':self.review_identity(session),'manifest':session['manifest'],'options':session['options'],'target_check':session.get('target_check'),
                        'session': token, 'state': 'needs_review', 'approval_required': True,
                        'plan': fresh, 'can_install': fresh['exit_code'] == 0 and bool(fresh['scope']),
                        'name': session['manifest']['name'], 'version': session['manifest'].get('version', '0'),
                        'archive_sha256': session['archive_sha256']}
            session.update(approved_scope=scope, approved_target=self.target_identity(target), state='running', backend_id=self.backend_id, started_at=timestamp(),
                           result=None, plan=fresh, log_file=f"attempt-{len(session['attempts']) + 1}.log")
            session['attempts'].append({'started_at': session['started_at'], 'log_file': session['log_file']})
            previous_control = session.get('control') or {'rate_mbps': 5}
            session['control'] = {'action': 'run', 'rate_mbps': previous_control['rate_mbps']}
            session['control_file'] = session['directory'] / 'download-control.json'
            atomic_json(session['control_file'], session['control'])
            atomic_json(session['directory'] / 'approved-scope.json', scope)
            self.persist(session)
            try:
                with (session['directory'] / session['log_file']).open('wb') as log:
                    process = await asyncio.create_subprocess_exec(
                        self.python, '-u', '-m', 'renderrepo.installation_worker', str(session['directory']),
                        str(global_lock.fileno()), str(job_lock.fileno()),
                        pass_fds=(global_lock.fileno(), job_lock.fileno()),
                        stdin=asyncio.subprocess.DEVNULL, stdout=log, stderr=asyncio.subprocess.STDOUT,
                        start_new_session=True)
            except BaseException:
                session.update(state='interrupted', result={'exit_code': 2, 'log': 'Worker launch interrupted; resume from history.'})
                self.persist(session)
                raise
            session['process'] = process
            async def wait():
                # Cancelling the browser/backend waiter does not cancel installation.
                await process.wait()
                return self.get(token)
            session['task'] = asyncio.create_task(wait())
            self.sessions[token]=session
        return {'session': token, 'state': 'running'}

    def control_download(self, token, values):
        session = self.get(token)
        if session['state'] != 'running' or 'control_file' not in session:
            raise ValueError('No installation is running in this session.')
        if not isinstance(values, dict) or set(values) != {'action', 'rate_mbps'}:
            raise ValueError('Expected action and rate_mbps.')
        if values['action'] not in {'run', 'pause', 'cancel'}:
            raise ValueError('Unknown download action.')
        rate = values['rate_mbps']
        if type(rate) not in {int, float} or not 0 <= rate <= 10000:
            raise ValueError('Rate must be between 0 and 10000 MB/s; 0 means unlimited.')
        if session['control']['action'] == 'cancel':
            raise ValueError('Cancellation has already been requested.')
        atomic_json(session['control_file'], values)
        session['control'] = values
        return values

    def status(self, token):
        session = self.get(token)
        if session.get('durable'):
            logs = []
            last_activity = session['updated_at']
            for attempt in session['attempts']:
                name = attempt.get('log_file', '')
                if not re.fullmatch(r'attempt-\d+\.log', name):
                    raise ValueError('Invalid installation log reference.')
                path = session['directory'] / name
                if path.is_file():
                    with path.open('rb') as stream:
                        stream.seek(max(0, path.stat().st_size - 240000))
                        logs.append(stream.read().decode('utf-8', errors='replace'))
                    last_activity = max(last_activity, datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat())
            session['progress'] = '\n'.join(logs)[-240000:]
            session['transfer'] = None
            for line in session['progress'].splitlines():
                if line.startswith('RR_TRANSFER '):
                    try:
                        session['transfer'] = json.loads(line[12:])
                    except ValueError:
                        pass
            control_file = session['directory'] / 'download-control.json'
            if control_file.exists():
                session['control'] = json.loads(control_file.read_text())
            destinations = (session.get('plan', {}).get('scope') or session.get('destination_scope') or {})
            workflow_path = destinations.get('workflow', {}).get('path')
            workflow_available = bool(workflow_path and Path(workflow_path).is_file()
                and hashlib.sha256(Path(workflow_path).read_bytes()).hexdigest() ==
                destinations.get('workflow', {}).get('sha256'))
            elapsed = int((datetime.now(timezone.utc) - datetime.fromisoformat(session.get('started_at', session['created_at']))).total_seconds())
        else:
            workflow_path, workflow_available, last_activity = None, False, session.get('created_at')
            elapsed = int(time.monotonic() - session.get('started', session['created']))
        result = session['result']
        previous_worker = bool(session.get('durable') and session['state'] == 'running'
                               and session.get('backend_id') != self.backend_id)
        return {**self.store_scope(session), 'capabilities': session.get('capabilities'), 'state': 'interrupted' if previous_worker else session['state'], 'result': result,
                'worker_active': session['state'] == 'running',
                'outcome': 'interrupted' if previous_worker else session.get('outcome'), 'workflow_available': workflow_available,
                'workflow_path': workflow_path, 'last_activity': last_activity,
                'options': session.get('options'), 'plan': session.get('plan'),
                'review_id':self.review_identity(session), 'manifest': session.get('manifest'), 'target_check':session.get('target_check'),
                'imported_workflow':session.get('imported_workflow'),
                'capsule':self.capsule_identity(session), 'predecessor_provenance':session.get('predecessor_provenance'),
                'attempts': session.get('attempts', []),
                'restart_required': bool(result and 'RESTART REQUIRED:' in result['log']),
                'progress': session.get('progress', ''),
                'transfer': session.get('transfer'),
                'control': session.get('control'),
                'elapsed_seconds': elapsed}

    def workflow(self, token):
        status = self.status(token)
        if not status['workflow_available']:
            raise ValueError('Workflow installation has not completed.')
        return Path(status['workflow_path']).read_bytes()

    async def export(self, values):
        if not isinstance(values, dict) or set(values) - {'workflow', 'name', 'version', 'custom_nodes', 'bundle_inputs', 'discovery'}:
            raise ValueError('Invalid export request.')
        if self.busy or (self.install_lock.exists() and active(self.install_lock)):
            raise ValueError('Wait for the running installation before exporting.')
        workflow = values.get('workflow')
        if not isinstance(workflow, dict) or not isinstance(workflow.get('nodes'), list) or not workflow['nodes']:
            raise ValueError('Export requires a nonempty current editor workflow.')
        name, version = values.get('name'), values.get('version', '0.1.0')
        if not isinstance(name, str) or not name.strip() or not isinstance(version, str):
            raise ValueError('Package name and version must be strings.')
        nodes, inputs = values.get('custom_nodes', []), values.get('bundle_inputs', [])
        if not all(isinstance(items, list) and all(isinstance(x, str) for x in items) for items in (nodes, inputs)):
            raise ValueError('Select custom-node and input names explicitly.')
        token, session = self.new_session()
        try:
            graph = session['directory'] / 'workflow.json'
            graph.write_text(json.dumps(workflow, ensure_ascii=False), encoding='utf-8')
            archive = session['directory'] / 'export.rrpkg'
            argv = ['pack', graph, '--output', archive, '--name', name, '--version', version, '--comfy-dir', self.root]
            if values.get('discovery'):
                from .discovery import validate_discovery
                metadata = session['directory'] / 'discovery.json'
                metadata.write_text(json.dumps(validate_discovery(values['discovery'])))
                argv += ['--discovery', metadata]
            for directory in self.artifact_dirs:
                argv += ['--artifact-dir', directory]
            for node in nodes:
                argv += ['--custom-node', node]
            for item in inputs:
                argv += ['--bundle-input', item]
            session.update(state='running', archive=archive, started=time.monotonic(),
                           progress='Export started: inspecting dependencies and hashing available files.')
            self.busy = True
            print('[RenderRepo] Export started: inspecting dependencies and hashing available files.', flush=True)
            def progress(text):
                session['progress'] = (session['progress'] + '\n' + text)[-240000:]
                print('[RenderRepo] ' + text, end='', flush=True)
            async def heartbeat():
                while True:
                    await asyncio.sleep(5)
                    print(f"[RenderRepo] Export still running ({int(time.monotonic() - session['started'])}s); inspecting/hashing dependencies.", flush=True)
            async def pack():
                pulse = asyncio.create_task(heartbeat())
                try:
                    session['result'] = await self.command(argv, progress)
                except asyncio.TimeoutError:
                    session['result'] = {'exit_code': 2, 'log': 'Export worker timed out after one hour and was terminated.'}
                except Exception as error:
                    session['result'] = {'exit_code': 2, 'log': str(error)}
                finally:
                    pulse.cancel()
                    session['state'] = 'exported' if session['result']['exit_code'] == 0 else 'done'
                    self.busy = False
                    print(f"[RenderRepo] Export finished: exit {session['result']['exit_code']}", flush=True)
            session['task'] = asyncio.create_task(pack())
            return {'session': token, 'state': 'running'}
        except BaseException:
            self.discard(token)
            raise



def allowed_request(request, *, require_header=True):
    # The bridge grants filesystem/code operations. Keep it on a local Comfy session.
    try:
        if not ipaddress.ip_address(request.remote).is_loopback:
            return False
    except ValueError:
        return False
    if require_header and request.headers.get('X-RenderRepo-Request') != '1':
        return False
    origin = request.headers.get('Origin')
    return (request.headers.get('Sec-Fetch-Site') not in {'cross-site', 'same-site'}
            and (not origin or (urlsplit(origin).netloc == request.host and urlsplit(origin).scheme in {'http', 'https'})))


def register(routes, folder_paths):
    from aiohttp import web
    roots = [folder_paths.get_input_directory()]
    for name, (paths, _) in folder_paths.folder_names_and_paths.items():
        if name != 'custom_nodes':
            roots.extend(paths)
    ui = PackageUI(folder_paths.base_path, roots, input_dir=folder_paths.get_input_directory())
    def route(method, path, *, static_asset=False):
        def decorate(handler):
            async def wrapped(request):
                log = logging.getLogger(__name__)
                log.info('RenderRepo request: %s %s', request.method, request.path)
                if not allowed_request(request, require_header=not static_asset):
                    log.warning('RenderRepo rejected: %s %s -> 403 (local same-origin session required)', request.method, request.path)
                    return web.json_response({'error': 'RR requires a local same-origin Comfy session.'}, status=403)
                try:
                    if getattr(request,'transport',None): ui.target_url = local_server(request)
                    response = await handler(request)
                    log.info('RenderRepo completed: %s %s -> %s', request.method, request.path, response.status)
                    return response
                except (ValueError, OSError, zipfile.BadZipFile, json.JSONDecodeError, YAMLError) as error:
                    log.warning('RenderRepo rejected: %s %s -> 400: %s', request.method, request.path, error)
                    return web.json_response({'error': str(error)}, status=400)
                except Exception:
                    log.exception('RenderRepo failed: %s %s -> 500', request.method, request.path)
                    return web.json_response({'error': 'Unexpected backend error; inspect the Comfy traceback for this operation and check installation history before retrying.'}, status=500)
            getattr(routes, method)('/renderrepo' + path)(wrapped)
            return wrapped
        return decorate

    @route('get', '/storefront/{asset}', static_asset=True)
    async def storefront_asset(request):
        asset = request.match_info['asset']
        if asset not in {'index.html', 'style.css', 'app.js', 'adapter.js'}:
            raise ValueError('Unknown storefront asset.')
        return web.FileResponse(Path(__file__).parent / 'storefront' / asset)

    async def body(request):
        # Do not depend on Comfy's global request-size setting.
        chunks, size = [], 0
        async for chunk in request.content.iter_chunked(65536):
            size += len(chunk)
            if size > MAX_UPLOAD:
                raise ValueError('Request exceeds 64 MiB.')
            chunks.append(chunk)
        return b''.join(chunks)

    @route('get', '/config')
    async def config(request):
        nodes = await asyncio.to_thread(ui.node_inventory)
        return web.json_response({'python': ui.python, 'python_prefix': sys.prefix, 'artifact_dirs': ui.artifact_dirs,
                                  'custom_nodes': nodes,
                                  'installations': ui.history(), 'environment': {'name':'This Comfy environment','root':str(ui.root),'capabilities':ui.target_capabilities}})

    @route('get', '/history')
    async def history(request):
        return web.json_response({'installations': ui.history(), 'environment': {'name':'This Comfy environment','root':str(ui.root),'capabilities':ui.target_capabilities}})

    async def storefront_status(data):
        # One small target inventory for the listing, never model acquisition or hashing.
        if ui.target_url:
            from .readiness import snapshot
            observed=await asyncio.to_thread(snapshot,ui.target_url,5)
            available=observed.get('nodes')
        else:
            available=None
        for item in data.get('packages',[]):
            required=(item.get('dependency_inventory') or {}).get('nodes')
            if available is None or not required:
                item['target_status']='Requirements unknown'
            elif all(node in available for node in required):
                item['target_status']='Required nodes available'
            elif ui.target_capabilities['install_nodes']:
                item['target_status']='Missing nodes — review installation sources'
            else:
                item['target_status']='Required nodes unavailable on this target'
        return data

    @route('get', '/repositories')
    async def repositories(request):
        return web.json_response(await storefront_status(await asyncio.to_thread(ui.repositories)))

    @route('get', '/local-tests/capabilities')
    async def local_test_capabilities(request):
        import nodes
        from server import PromptServer
        return web.json_response({'root':str(ui.root), 'input_dir':str(ui.input_dir),
            'output_dir':str(Path(folder_paths.get_output_directory()).resolve()),
            'modules':{name:getattr(nodes.NODE_CLASS_MAPPINGS.get(name),'__module__',None)
                       for name in ('LoadImage','SaveImage','ImageScale','ImageCompositeMasked')},
            'targeted_cancel':callable(getattr(PromptServer.instance.prompt_queue,'interrupt_if_running',None))})

    def local_server(request):
        address=request.transport.get_extra_info('sockname')
        host='[::1]' if ':' in address[0] else '127.0.0.1'
        return f'http://{host}:{address[1]}'

    @route('post', '/local-tests/plan')
    async def local_test_plan(request):
        from .local_test import prepare
        values=json.loads(await body(request))
        return web.json_response(await asyncio.to_thread(prepare, ui.root, values.get('name'),local_server(request),
            runs=values.get('runs',1),deadline=values.get('deadline',60),input_dir=ui.input_dir,
            output_dir=folder_paths.get_output_directory()))

    @route('get', '/local-tests')
    async def local_tests(request):
        from .local_test import store, status
        def listing():
            result=[]
            for path in sorted(store(ui.root).glob('*/state.json'),reverse=True):
                try: result.append(status(ui.root,path.parent.name,True))
                except (ValueError,OSError) as error: result.append({'id':path.parent.name,'state':'unresolved','error':str(error)})
            return {'tests':result}
        return web.json_response(await asyncio.to_thread(listing))

    @route('get', '/local-tests/{token}')
    async def local_test_status(request):
        from .local_test import status
        return web.json_response(await asyncio.to_thread(status,ui.root,request.match_info['token'],True))

    @route('post', '/local-tests/{token}/start')
    async def local_test_start(request):
        from .local_test import approve, launch, status
        token=request.match_info['token'];values=json.loads(await body(request))
        await asyncio.to_thread(approve,ui.root,token,values.get('approval_sha256'))
        launch(ui.root,token,ui.python)
        return web.json_response(await asyncio.to_thread(status,ui.root,token))

    @route('post', '/local-tests/{token}/stop')
    async def local_test_stop(request):
        from .local_test import stop
        return web.json_response(await asyncio.to_thread(stop,ui.root,request.match_info['token']))

    @route('post', '/local-tests/{token}/reconcile')
    async def local_test_reconcile(request):
        from .local_test import reconcile
        return web.json_response(await asyncio.to_thread(reconcile,ui.root,request.match_info['token']))

    @route('post', '/local-tests/{token}/cancel-owned')
    async def cancel_owned_test(request):
        from .local_test import cancel_owned
        from server import PromptServer
        values=json.loads(await body(request))
        return web.json_response(cancel_owned(ui.root,request.match_info['token'],values.get('prompt_id'),PromptServer.instance.prompt_queue))

    @route('post', '/readiness')
    async def readiness(request):
        from .readiness import snapshot
        values = json.loads(await body(request))
        if not isinstance(values, dict):
            raise ValueError('Choose a package for readiness inspection.')
        # Inspect this same server, never a publisher URL or a caller-selected host.
        address = request.transport.get_extra_info('sockname')
        host = '[::1]' if ':' in address[0] else '127.0.0.1'
        observed = await asyncio.to_thread(snapshot, f'http://{host}:{address[1]}', 5)
        return web.json_response(await asyncio.to_thread(ui.readiness, values, observed))

    @route('get', '/installed-workflow')
    async def installed_workflow(request):
        name = request.query.get('name')
        if not name:
            raise ValueError('Choose an installed package to open its workflow.')
        return web.Response(body=await asyncio.to_thread(ui.installed_workflow, name), content_type='application/json')

    @route('post', '/repositories/source')
    async def repository_source(request):
        return web.json_response(await storefront_status(await asyncio.to_thread(ui.repository_source, json.loads(await body(request)))))

    @route('post', '/repositories/refresh')
    async def repository_refresh(request):
        return web.json_response(await ui.repository_refresh(json.loads(await body(request))))

    @route('post', '/package-link')
    async def package_link(request):
        return web.json_response(await ui.retrieve_link(json.loads(await body(request))))

    @route('post', '/repositories/retrieve')
    async def repository_retrieve(request):
        return web.json_response(await ui.repository_retrieve(json.loads(await body(request))))

    @route('get', '/repositories/operation/{token}')
    async def repository_operation(request):
        return web.json_response(ui.repository_operation(request.match_info['token']))

    @route('post', '/resume/{token}')
    async def resume(request):
        if json.loads(await body(request)) != {}:
            raise ValueError('Resume takes no new options; review to change them.')
        return web.json_response(await ui.resume(request.match_info['token']))

    @route('post', '/preview')
    async def preview(request):
        data=await body(request)
        if data.lstrip().startswith((b'{',b'[')):
            return web.json_response(await ui.preview_workflow(data, unquote(request.headers.get('X-RenderRepo-Filename','workflow.json'))))
        return web.json_response(await ui.preview(data, {}))

    @route('post', '/local-files')
    async def local_files(request):
        values = json.loads(await body(request))
        return web.json_response(await asyncio.to_thread(ui.search_local_files, values))

    @route('post', '/resolve/{token}')
    async def resolve_requirement(request):
        return web.json_response(await ui.resolve_requirement(request.match_info['token'],json.loads(await body(request))))

    @route('post', '/review/{token}')
    async def review(request):
        return web.json_response(await ui.review(request.match_info['token'], json.loads(await body(request))))

    @route('post', '/install/{token}')
    async def install(request):
        values = json.loads(await body(request))
        if not isinstance(values, dict) or not {'approved','review_id'} <= set(values) or set(values) - {'approved','review_id','confirmations'} or not isinstance(values.get('review_id'),str):
            raise ValueError('Installation requires explicit approval of the current review identity.')
        confirmations = values.get('confirmations', [])
        if not isinstance(confirmations, list):
            raise ValueError('Installation confirmations must be a list.')
        return web.json_response(await ui.approve(request.match_info['token'], values.get('approved'), review_id=values['review_id'], confirmations=confirmations))

    @route('get', '/status/{token}')
    async def status(request):
        return web.json_response(ui.status(request.match_info['token']))

    @route('post', '/download-control/{token}')
    async def download_control(request):
        return web.json_response(ui.control_download(request.match_info['token'], json.loads(await body(request))))

    @route('get', '/example/{token}/{index}')
    async def example(request):
        session = ui.get(request.match_info['token'])
        inputs = session['manifest'].get('inputs', [])
        index = int(request.match_info['index'])
        if not 0 <= index < len(inputs):
            raise ValueError('Example not available.')
        item = inputs[index]
        if item.get('mode') != 'bundled' or item.get('kind') != 'image':
            raise ValueError('Only supplied bundled images are previews.')
        artifact = next((a for a in session['manifest']['artifacts'] if a['filename'] == item['filename']), None)
        if artifact is None:
            raise ValueError('No bundled artifact for this example.')
        source = next((s['uri'][8:] for s in artifact.get('sources', []) if s['uri'].startswith('package:')), None)
        path = (session['package'] / source).resolve() if source else None
        if not path or not path.is_relative_to(session['package'].resolve()) or path.suffix.lower() not in {'.png','.jpg','.jpeg','.webp','.gif'} or path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError('No supported small preview supplied.')
        return web.FileResponse(path)

    @route('get', '/workflow/{token}')
    async def workflow(request):
        return web.Response(body=ui.workflow(request.match_info['token']), content_type='application/json')

    @route('post', '/discard/{token}')
    async def discard(request):
        ui.discard(request.match_info['token'])
        return web.json_response({'discarded': True})

    @route('post', '/export')
    async def export(request):
        return web.json_response(await ui.export(json.loads(await body(request))))

    @route('get', '/archive/{token}')
    async def archive(request):
        session = ui.get(request.match_info['token'])
        if session['state'] != 'exported':
            raise ValueError('No exported archive in this session.')
        return web.Response(body=session['archive'].read_bytes(), content_type='application/zip')

    return ui

