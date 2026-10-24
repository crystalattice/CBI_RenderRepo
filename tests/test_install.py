import contextlib
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import yaml

from renderrepo.cli import main


class DownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                cls.requests.append(self.path)
                if self.path == "/404":
                    self.send_error(404)
                    return
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "/good")
                    self.end_headers()
                    return
                if self.path == "/redirect-file":
                    self.send_response(302)
                    self.send_header("Location", "file:///etc/passwd")
                    self.end_headers()
                    return
                body = b"bad" if self.path == "/bad" else b"good"
                self.send_response(200)
                self.send_header("Content-Type", "text/html" if self.path == "/html" else "application/octet-stream")
                if self.path != "/no-length":
                    self.send_header("Content-Length", "100" if self.path == "/short" else str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                self.close_connection = True
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.requests.clear()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / "package"
        self.package.mkdir()
        (self.package / "workflow.json").write_text(json.dumps({
            "1": {"class_type": "LoadImage", "inputs": {"image": "input.png"}}}))
        self.destination = self.root / "downloads"
        self.artifact = {"filename": "input.png", "size_bytes": 4,
                         "sha256": hashlib.sha256(b"good").hexdigest(),
                         "sources": [{"uri": self.url + "/good"}]}
        self.manifest = {"schema_version": 0, "name": "test", "workflow": "workflow.json",
                         "artifacts": [self.artifact]}
        self.write_manifest()


    def test_pack_http_download_to_comfy_end_to_end(self):
        source = self.root / 'export.json'
        source.write_text(json.dumps({'nodes': [{
            'id': 1, 'type': 'UNETLoader', 'widgets_values': ['tiny.safetensors'],
            'properties': {'cnr_id': 'comfy-core', 'ver': 'test', 'models': [{
                'name': 'tiny.safetensors', 'directory': 'diffusion_models', 'url': self.url + '/good'}]}}],
            'links': []}))
        packaged = self.root / 'packed'
        comfy = self.root / 'ComfyUI'
        comfy.mkdir()
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            self.assertEqual(main(['pack', str(source), '--output', str(packaged)]), 0)
            # The exporter cannot obtain a checksum without installed weights;
            # here emulate publisher-supplied verified metadata for the tiny HTTP fixture.
            from renderrepo.capsule import copy_capsule
            def publisher_fingerprint(folder):
                manifest = yaml.safe_load((folder / 'manifest.yaml').read_text())
                manifest['artifacts'][0].update(size_bytes=4, sha256=hashlib.sha256(b'good').hexdigest())
                (folder / 'manifest.yaml').write_text(json.dumps(manifest))
            # Publisher edits create a new linked revision, never mutate a sealed capsule.
            packaged = copy_capsule(packaged, self.root / 'publisher-verified', edit=publisher_fingerprint)
            with patch('builtins.input', return_value='n'):
                self.assertEqual(main(['install', str(packaged), '--comfy-dir', str(comfy)]), 1)
            self.assertEqual(self.requests, [])
            self.assertEqual(list(comfy.iterdir()), [])
            self.assertEqual(main(['install', str(packaged), '--comfy-dir', str(comfy), '--yes']), 0)
            self.assertEqual(main(['inspect', str(packaged), '--artifact-dir', str(comfy / 'models')]), 0)
        self.assertEqual(self.requests, ['/good'])
        self.assertEqual((comfy / 'models/diffusion_models/tiny.safetensors').read_bytes(), b'good')
        self.assertEqual((comfy / 'user/default/workflows/export.json').read_bytes(), source.read_bytes())


    def test_portable_archive_http_handoff_and_removal(self):
        author = self.root / 'author'
        author.mkdir()
        (author / 'tiny.safetensors').write_bytes(b'good')
        (author / 'example.png').write_bytes(b'image')
        workflow = author / 'export.json'
        workflow.write_text(json.dumps({'nodes': [
            {'id': 1, 'type': 'UNETLoader', 'widgets_values': ['tiny.safetensors'],
             'properties': {'models': [{'name': 'tiny.safetensors', 'directory': 'diffusion_models',
                                       'url': self.url + '/good'}]}},
            {'id': 2, 'type': 'LoadImage', 'widgets_values': ['example.png', 'image']}], 'links': []}))
        portable = self.root / 'receiver/demo.rrpkg'
        comfy = self.root / 'receiver/ComfyUI'
        comfy.mkdir(parents=True)
        original = workflow.read_bytes()
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            self.assertEqual(main(['pack', str(workflow), '--output', str(portable),
                '--artifact-dir', str(author), '--bundle-input', 'example.png', '--name', 'portable']), 0)
            import shutil
            shutil.rmtree(author)
            with patch('builtins.input', return_value='n'):
                self.assertEqual(main(['install', str(portable), '--comfy-dir', str(comfy)]), 1)
            self.assertEqual(self.requests, [])
            self.assertEqual(list(comfy.iterdir()), [])
            self.assertEqual(main(['install', str(portable), '--comfy-dir', str(comfy), '--yes']), 0)
            self.assertEqual(main(['list', '--comfy-dir', str(comfy)]), 0)
            self.assertEqual((comfy / 'user/default/workflows/portable.json').read_bytes(), original)
            self.assertEqual((comfy / 'input/example.png').read_bytes(), b'image')
            self.assertEqual(main(['inspect', str(portable), '--artifact-dir', str(comfy / 'models'),
                                   '--artifact-dir', str(comfy / 'input')]), 0)
            self.assertEqual(main(['remove', 'portable', '--comfy-dir', str(comfy), '--yes']), 0)
        self.assertEqual(self.requests, ['/good'])
        self.assertEqual((comfy / 'models/diffusion_models/tiny.safetensors').read_bytes(), b'good')
        self.assertFalse((comfy / 'user/default/workflows/portable.json').exists())
        self.assertFalse((comfy / 'input/example.png').exists())

    def write_manifest(self):
        (self.package / "manifest.yaml").write_text(yaml.safe_dump(self.manifest))

    def run_cli(self, *extra, answers=()):
        out, err = io.StringIO(), io.StringIO()
        with patch("builtins.input", side_effect=answers), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["install", str(self.package), "--destination", str(self.destination), *extra])
        return code, out.getvalue(), err.getvalue()

    def assert_clean(self):
        self.assertEqual(list(self.destination.glob("*.part")), [])

    def test_confirmed_download_and_reinspect(self):
        code, out, _ = self.run_cli(answers=["y"])
        self.assertEqual(code, 0)
        self.assertIn("DOWNLOAD: input.png", out)
        self.assertIn("DOWNLOADED:", out)
        self.assertEqual((self.destination / "input.png").read_bytes(), b"good")
        self.assertEqual(self.requests, ["/good"])
        self.assert_clean()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["inspect", str(self.package), "--artifact-dir", str(self.destination)]), 0)

    def test_decline_does_not_request_or_create_destination(self):
        self.assertEqual(self.run_cli(answers=["n"])[0], 1)
        self.assertEqual(self.requests, [])
        self.assertFalse(self.destination.exists())

    def test_blank_confirmation_declines(self):
        self.assertEqual(self.run_cli(answers=[""])[0], 1)
        self.assertEqual(self.requests, [])

    def test_eof_declines(self):
        with patch("builtins.input", side_effect=EOFError), contextlib.redirect_stdout(io.StringIO()):
            code = main(["install", str(self.package), "--destination", str(self.destination)])
        self.assertEqual(code, 1)
        self.assertEqual(self.requests, [])

    def test_yes_and_reuse_destination(self):
        self.assertEqual(self.run_cli("--yes")[0], 0)
        self.requests.clear()
        code, out, _ = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("nothing to download", out)
        self.assertEqual(self.requests, [])

    def test_reuse_existing_search_root(self):
        existing = self.root / "existing"
        existing.mkdir()
        (existing / "input.png").write_bytes(b"good")
        self.assertEqual(self.run_cli("--artifact-dir", str(existing))[0], 0)
        self.assertEqual(self.requests, [])
        self.assertFalse(self.destination.exists())

    def test_manual_url_override(self):
        self.artifact["sources"] = [{"uri": self.url + "/404"}]
        self.write_manifest()
        self.assertEqual(self.run_cli("--yes", "--source", "input.png=" + self.url + "/good")[0], 0)
        self.assertEqual(self.requests, ["/good"])

    def test_interactive_source_when_unknown(self):
        del self.artifact["sources"]
        self.write_manifest()
        self.assertEqual(self.run_cli(answers=[self.url + "/good", "yes"])[0], 0)

    def test_unresolved_source_prevents_all_downloads(self):
        self.manifest["artifacts"].append({"filename": "unknown.bin"})
        self.write_manifest()
        code, _, err = self.run_cli("--yes")
        self.assertEqual(code, 1)
        self.assertIn("NO SOURCE: unknown.bin", err)
        self.assertEqual(self.requests, [])
        self.assertFalse(self.destination.exists())

    def test_cancel_source_prevents_downloads(self):
        del self.artifact["sources"]
        self.write_manifest()
        self.assertEqual(self.run_cli(answers=[""])[0], 1)
        self.assertEqual(self.requests, [])

    def test_ordered_mirror_fallback(self):
        self.artifact["sources"] = [{"uri": self.url + suffix} for suffix in ("/404", "/bad", "/good")]
        self.write_manifest()
        self.assertEqual(self.run_cli("--yes")[0], 0)
        self.assertEqual(self.requests, ["/404", "/bad", "/good"])
        self.assertEqual((self.destination / "input.png").read_bytes(), b"good")
        self.assert_clean()

    def test_hash_failure_preserves_old_destination(self):
        self.destination.mkdir()
        (self.destination / "input.png").write_bytes(b"old!")
        self.artifact["sources"] = [{"uri": self.url + "/bad"}]
        self.artifact.pop("size_bytes")
        self.write_manifest()
        code, out, err = self.run_cli("--yes")
        self.assertEqual(code, 1)
        self.assertIn("replace existing destination file", out)
        self.assertIn("sha256", err)
        self.assertEqual((self.destination / "input.png").read_bytes(), b"old!")
        self.assert_clean()

    def test_size_failure_leaves_no_final_file(self):
        self.artifact["sources"] = [{"uri": self.url + "/bad"}]
        self.write_manifest()
        self.assertEqual(self.run_cli("--yes")[0], 1)
        self.assertFalse((self.destination / "input.png").exists())
        self.assert_clean()

    def test_short_download_rejected_even_without_manifest_checks(self):
        self.artifact.pop("sha256")
        self.artifact.pop("size_bytes")
        self.artifact["sources"] = [{"uri": self.url + "/short"}]
        self.write_manifest()
        self.assertEqual(self.run_cli("--yes")[0], 1)
        self.assertFalse((self.destination / "input.png").exists())
        self.assert_clean()

    def test_no_content_length_still_verifies(self):
        self.artifact["sources"] = [{"uri": self.url + "/no-length"}]
        self.write_manifest()
        self.assertEqual(self.run_cli("--yes")[0], 0)

    def test_html_is_not_installed_as_model(self):
        self.artifact["sources"] = [{"uri": self.url + "/html"}]
        self.write_manifest()
        self.assertEqual(self.run_cli("--yes")[0], 1)
        self.assertFalse((self.destination / "input.png").exists())

    def test_redirect_download(self):
        self.artifact["sources"] = [{"uri": self.url + "/redirect"}]
        self.write_manifest()
        self.assertEqual(self.run_cli("--yes")[0], 0)
        self.assertEqual(self.requests, ["/redirect", "/good"])

    def test_redirect_to_local_file_rejected(self):
        self.artifact["sources"] = [{"uri": self.url + "/redirect-file"}]
        self.write_manifest()
        self.assertEqual(self.run_cli("--yes")[0], 1)
        self.assertFalse((self.destination / "input.png").exists())

    def test_local_file_uri_and_manual_path(self):
        local = self.root / "file with %20 spaces.bin"
        local.write_bytes(b"good")
        for source in (str(local), local.as_uri()):
            with self.subTest(source=source):
                self.assertEqual(self.run_cli("--yes", "--source", "input.png=" + source)[0], 0)
                (self.destination / "input.png").unlink()
        self.assertEqual(self.requests, [])

    def test_invalid_override_and_protocol(self):
        for source in ("unknown.bin=" + self.url + "/good", "input.png=ftp://example.org/model"):
            with self.subTest(source=source):
                self.assertEqual(self.run_cli("--yes", "--source", source)[0], 2)
                self.assertEqual(self.requests, [])

    def test_partial_batch_keeps_success_and_retries_only_failure(self):
        second = dict(self.artifact, filename="second.bin", sources=[{"uri": self.url + "/404"}])
        self.manifest["artifacts"].append(second)
        self.write_manifest()
        self.assertEqual(self.run_cli("--yes")[0], 1)
        self.assertEqual((self.destination / "input.png").read_bytes(), b"good")
        self.requests.clear()
        self.assertEqual(self.run_cli("--yes", "--source", "second.bin=" + self.url + "/good")[0], 0)
        self.assertEqual(self.requests, ["/good"])

    def test_interrupt_retains_checksummed_partial_and_rerun_verifies_it(self):
        with patch("renderrepo.download.os.fsync", side_effect=KeyboardInterrupt):
            self.assertEqual(self.run_cli("--yes")[0], 130)
        self.assertFalse((self.destination / "input.png").exists())
        partials = list(self.destination.glob('*.part'))
        self.assertEqual(len(partials), 1)
        self.assertEqual(partials[0].stat().st_size, self.artifact['size_bytes'])
        self.requests.clear()
        self.assertEqual(self.run_cli('--yes')[0], 0)
        self.assertEqual(self.requests, [])
        self.assert_clean()


if __name__ == "__main__":
    unittest.main()

