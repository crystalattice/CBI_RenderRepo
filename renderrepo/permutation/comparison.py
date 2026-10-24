"""Local review of completed candidates across experiments; never runs a provider."""
import csv
import hashlib
import html
import io
import json
from pathlib import Path
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from . import engine


class Comparison:
    def __init__(self, manifest):
        self.path = Path(manifest).resolve()
        self.root = self.path.parent
        self.value = engine.read(self.path)
        self.sources = {}
        for candidate in self.value['candidates']:
            if 'source_directory' in candidate:
                directory = Path(candidate['source_directory']).resolve()
            elif candidate['new_submission_required']:
                directory = Path(self.value['new_plan_directory']).resolve()
            else:
                summary = Path(candidate['source_export']).parent.parent / 'plan-summary.json'
                directory = Path(engine.read(summary)['directory']).resolve()
            if directory.name != candidate['source_experiment']:
                raise ValueError('Candidate source experiment does not match its directory.')
            key = (directory.name, candidate['record']['id'])
            if key in self.sources:
                raise ValueError('Duplicate comparison candidate.')
            self.sources[key] = (directory, candidate['new_submission_required'])
        self.lock = threading.Lock()

    def snapshot(self):
        value = json.loads(json.dumps(self.value))
        for candidate in value['candidates']:
            key = (candidate['source_experiment'], candidate['record']['id'])
            directory, editable = self.sources[key]
            row = next((r for r in engine.results(directory) if r['id'] == key[1]), None)
            if not row or row['state'] != 'completed':
                raise ValueError('Comparison candidate is unavailable or not completed.')
            candidate['source_directory'] = str(directory)
            candidate['record'] = row
            candidate['editable'] = editable
            candidate['output_files'] = [{'original_path': str(directory/'jobs'/row['id']/o['file']),
                                         'sha256': o['sha256'],
                                         'url': f'/image/{key[0]}/{key[1]}/{o["file"]}'} for o in row['outputs']]
        return value

    def save(self, experiment, job, rating, notes):
        source = self.sources.get((experiment, job))
        if not source or not source[1]:
            raise ValueError('This candidate is a read-only reused baseline. Rate only the new results.')
        with self.lock:
            engine.review(source[0], job, rating, notes)
            try:
                value = self.export()
            except (OSError, ValueError) as error:
                raise ExportError('Rating saved, but export refresh failed. Reload to confirm the rating and check export-directory permissions.') from error
            return next(c['record']['review'] for c in value['candidates']
                        if c['source_experiment'] == experiment and c['record']['id'] == job)

    def export(self):
        value = self.snapshot()
        engine.atomic_json(self.path, value)
        buffer = io.StringIO(newline='')
        writer = csv.DictWriter(buffer, fieldnames=['kind', 'source_experiment', 'id', 'cfg', 'seed', 'state',
                                                   'new_submission_required', 'provider_job_id', 'rating', 'notes_json', 'provenance_json'])
        writer.writeheader()
        for c in value['candidates']:
            r = c['record']
            writer.writerow({'kind': c['kind'], 'source_experiment': c['source_experiment'], 'id': r['id'],
                             'cfg': r['settings'].get('cfg'), 'seed': r['settings']['seed'], 'state': r['state'],
                             'new_submission_required': c['new_submission_required'], 'provider_job_id': r['provider_job_id'],
                             'rating': r['review']['rating'], 'notes_json': json.dumps(r['review']['notes']),
                             'provenance_json': json.dumps(c, sort_keys=True)})
        engine.atomic_bytes(self.root/'comparison.csv', buffer.getvalue().encode())
        for directory in {d for d, editable in self.sources.values() if editable}:
            destination = self.root/'new-results-export' if len({d for d, edit in self.sources.values() if edit}) == 1 else self.root/'exports'/directory.name
            engine.export(directory, destination)
        engine.atomic_bytes(self.root/'comparison.html', self.render(value).encode())
        return value

    def render(self, value=None):
        value = value or self.snapshot()
        esc = html.escape
        parts = ['<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>CFG comparison review</title><link rel="stylesheet" href="/comparison-review.css"><script src="/comparison-review.js" defer></script><main><h1>CFG comparison review</h1>',
                 '<p>'+esc(value.get('status', 'Completed comparison'))+'</p>',
                 '<p>Grouped by seed. Reused baseline records are read-only. Actual charges remain unknown.</p>',
                 '<p><a href="/comparison.json">Provenance JSON</a> · <a href="/comparison.csv">CSV export</a></p>']
        for seed in sorted({c['record']['settings']['seed'] for c in value['candidates']}):
            parts.append(f'<h2>Seed {seed}</h2><section class="pair">')
            candidates = sorted((c for c in value['candidates'] if c['record']['settings']['seed'] == seed), key=lambda c: c['record']['settings'].get('cfg', 0))
            for c in candidates:
                r = c['record']; review = r['review']
                parts.append(f'<article data-experiment="{esc(c["source_experiment"])}" data-job="{esc(r["id"])}"><h3>CFG {esc(str(r["settings"].get("cfg")))} — {esc(c["kind"])}</h3>')
                for o in c['output_files']:
                    parts.append(f'<img src="{esc(o["url"])}" alt="CFG {r["settings"].get("cfg")} seed {seed}">')
                parts.append(f'<p>Rating: <strong data-current-rating>{esc(review["rating"])}</strong></p><p data-current-notes>{esc(review["notes"])}</p>')
                if c['editable']:
                    parts.append(f'<div data-review-controls><label>Review notes<textarea maxlength="1000">{esc(review["notes"])}</textarea></label>')
                    for rating in ('keeper', 'modify', 'toss', 'unreviewed'):
                        parts.append(f'<button disabled data-rating="{rating}" aria-pressed="{str(review["rating"] == rating).lower()}">{rating.title()}</button>')
                    parts.append('<button disabled data-save-notes>Save notes</button><p data-save-status role="status" aria-live="polite">Connecting to review server… If opened as a file, use the live comparison URL to save ratings.</p></div>')
                else:
                    parts.append('<p>Reused baseline: ratings and notes preserved; editing disabled here.</p>')
                parts.append('<details><summary>Source workflow, settings and provenance</summary><pre>'+esc(json.dumps(c, indent=2))+'</pre></details></article>')
            parts.append('</section>')
        return '\n'.join(parts)+ '</main></html>'


class ExportError(ValueError):
    pass


def server(manifest, port=8846):
    comparison = Comparison(manifest)
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, code, body, media='application/json'):
            self.send_response(code)
            self.send_header('Content-Type', media)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; frame-ancestors 'none'")
            self.end_headers(); self.wfile.write(body)

        def error(self, code, message, saved=False):
            self.send(code, json.dumps({'error': message, 'saved': saved}).encode())

        def allowed(self, mutation=False):
            host = f'127.0.0.1:{self.server.server_port}'
            return (self.headers.get('Host') == host and
                    self.headers.get('Sec-Fetch-Site', 'same-origin') in {'same-origin', 'none'} and
                    (not mutation or (self.headers.get('Origin') == 'http://'+host and self.headers.get('X-RR-Review') == token)))

        def do_GET(self):
            if not self.allowed():
                return self.error(403, 'Open the comparison using its exact http://127.0.0.1 URL.')
            path = urlsplit(self.path).path
            try:
                if path in {'/', '/comparison.html'}:
                    return self.send(200, comparison.render().encode(), 'text/html; charset=utf-8')
                if path == '/data':
                    value = comparison.snapshot()
                    return self.send(200, json.dumps({'token': token, 'candidates': value['candidates']}).encode())
                if path in {'/comparison-review.js', '/comparison-review.css'}:
                    return self.send(200, (Path(__file__).parent/path[1:]).read_bytes(), 'text/javascript' if path.endswith('.js') else 'text/css')
                if path in {'/comparison.json', '/comparison.csv'}:
                    with comparison.lock:
                        comparison.export()
                    return self.send(200, (comparison.root/path[1:]).read_bytes(), 'application/json' if path.endswith('.json') else 'text/csv')
                if path.startswith('/image/'):
                    _, _, experiment, job, name = path.split('/')
                    row = next(c for c in comparison.snapshot()['candidates'] if c['source_experiment'] == experiment and c['record']['id'] == job)
                    output = next(o for o in row['record']['outputs'] if o['file'] == name)
                    content = (Path(row['source_directory'])/'jobs'/job/name).read_bytes()
                    if hashlib.sha256(content).hexdigest() != output['sha256']:
                        raise ValueError('Output hash mismatch.')
                    return self.send(200, content, {'.png':'image/png', '.jpg':'image/jpeg', '.webp':'image/webp'}[Path(name).suffix])
                return self.error(404, 'Unknown comparison route.')
            except (ValueError, OSError, KeyError, StopIteration):
                return self.error(409, 'Comparison record unavailable. Check source files and output hashes.')

        def do_POST(self):
            if not self.allowed(True):
                return self.error(403, 'Review session expired or origin mismatched. Reload the exact comparison URL and save again.')
            if self.path != '/review':
                return self.error(404, 'Unknown review route.')
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 8192:
                    raise ValueError('Review request must be at most 8192 bytes.')
                body = json.loads(self.rfile.read(size))
                review = comparison.save(body['experiment'], body['job'], body['rating'], body.get('notes', ''))
                return self.send(200, json.dumps({'saved':True, 'review':review}).encode())
            except ExportError as error:
                return self.error(500, str(error), saved=True)
            except (ValueError, KeyError, TypeError) as error:
                return self.error(400, str(error))
            except OSError:
                return self.error(500, 'Could not persist review. Check permissions and free space in the source experiment directory.')

    http = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    http.comparison = comparison
    return http


def serve(manifest, port=8846):
    with server(manifest, port) as http:
        http.comparison.export()
        print(f'Comparison: http://127.0.0.1:{http.server_port}/', flush=True)
        try:
            http.serve_forever()
        except KeyboardInterrupt:
            pass
