import contextlib
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from renderrepo.download import fetch_artifact, TransferCancelled


class ManagedDownloadTests(unittest.TestCase):
    def test_http_interruption_resumes_and_verifies(self):
        payload = b'abcdefgh' * 32768
        ranges = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                value = self.headers.get('Range'); ranges.append(value)
                offset = int(value[6:-1]) if value else 0
                self.send_response(206 if value else 200)
                self.send_header('Content-Length', str(len(payload)-offset))
                if value: self.send_header('Content-Range', f'bytes {offset}-{len(payload)-1}/{len(payload)}')
                self.end_headers()
                self.wfile.write(payload[offset:] if value else payload[:65536])
                self.close_connection = True
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
                folder = Path(temporary)
                artifact = {'filename':'model.bin','size_bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}
                uri = f'http://127.0.0.1:{server.server_port}/model'
                with self.assertRaises(OSError):fetch_artifact(artifact,uri,folder,2,rate_limit=0)
                self.assertFalse((folder/'model.bin').exists())
                self.assertEqual(next(folder.glob('*.part')).stat().st_size,65536)
                target, size, digest = fetch_artifact(artifact,uri,folder,2,rate_limit=0)
                self.assertEqual(target.read_bytes(),payload)
                self.assertEqual(ranges,[None,'bytes=65536-'])
                self.assertFalse(list(folder.glob('*.part')))
        finally:server.shutdown();server.server_close();thread.join()

    def test_retry_connection_failure_reports_retained_partial_bytes(self):
        # A retry returning HTTP 503 must not erase the preceding byte count.
        from unittest.mock import patch
        from urllib.error import HTTPError
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            artifact = {'filename': 'input.bin', 'sha256': '0' * 64, 'size_bytes': 262144}
            uri = 'http://localhost/input.bin'
            key = hashlib.sha256(json.dumps([uri, artifact], sort_keys=True).encode()).hexdigest()
            partial = folder / ('.renderrepo-' + key + '.part')
            partial.write_bytes(b'x' * 65536)
            output = io.StringIO()
            with contextlib.redirect_stdout(output), patch('renderrepo.download.build_opener') as opener:
                opener.return_value.open.side_effect = HTTPError(uri, 503, 'Unavailable', {}, None)
                with self.assertRaises(HTTPError):
                    fetch_artifact(artifact, uri, folder, 2, folder / 'controls.json')
            progress = [json.loads(line.removeprefix('RR_TRANSFER ')) for line in output.getvalue().splitlines()]
            self.assertEqual(progress[-1]['bytes'], partial.stat().st_size)
            self.assertEqual(progress[-1]['bytes'], 65536)
            self.assertFalse((folder / 'input.bin').exists())

    def test_bad_hash_never_published_and_partial_discarded(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            folder=Path(temporary);source=folder/'source';source.write_bytes(b'bad')
            with self.assertRaisesRegex(ValueError,'sha256'):
                fetch_artifact({'filename':'model','sha256':'0'*64},source.as_uri(),folder,2,rate_limit=0)
            self.assertFalse((folder/'model').exists());self.assertFalse(list(folder.glob('*.part')))

    def test_pause_resume_rate_cap_and_cancel_keeps_partial(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            folder=Path(temporary);source=folder/'source';source.write_bytes(b'x'*262144)
            control=folder/'control.json';control.write_text(json.dumps({'action':'pause','rate_mbps':.1}))
            errors=[]
            def run():
                try:fetch_artifact({'filename':'model','sha256':hashlib.sha256(source.read_bytes()).hexdigest()},source.as_uri(),folder,2,control)
                except Exception as error:errors.append(error)
            thread=threading.Thread(target=run);thread.start()
            time.sleep(.2);self.assertFalse(list(folder.glob('*.part')))
            control.write_text(json.dumps({'action':'run','rate_mbps':.1}))
            deadline=time.monotonic()+2
            while not list(folder.glob('*.part')) and time.monotonic()<deadline:time.sleep(.01)
            time.sleep(.15)
            self.assertLessEqual(next(folder.glob('*.part')).stat().st_size,65536)
            control.write_text(json.dumps({'action':'cancel','rate_mbps':.1}))
            thread.join(2);self.assertFalse(thread.is_alive())
            self.assertIsInstance(errors[0],TransferCancelled)
            self.assertTrue(list(folder.glob('*.part')));self.assertFalse((folder/'model').exists())
