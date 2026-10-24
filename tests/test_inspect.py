import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

from renderrepo.cli import inspect_artifacts, load_package, main


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "wan2.2-i2v"


class InspectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / "package"
        self.package.mkdir()
        self.models = self.root / "models"
        self.models.mkdir()
        # Small unit fixture; the reference acceptance test uses the real export.
        (self.package / "workflow.json").write_text(json.dumps({
            "1": {"class_type": "LoadImage", "inputs": {"image": "input.png"}}}))
        self.manifest = {"schema_version": 0, "name": "test", "workflow": "workflow.json",
                         "artifacts": [{"filename": "input.png"}]}
        self.write_manifest()

    def write_manifest(self):
        (self.package / "manifest.yaml").write_text(yaml.safe_dump(self.manifest))

    def run_cli(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["inspect", str(self.package), *extra])
        return code, out.getvalue(), err.getvalue()

    def test_found_recursive(self):
        nested = self.models / "nested"
        nested.mkdir()
        (nested / "input.png").write_bytes(b"data")
        code, out, err = self.run_cli("--artifact-dir", str(self.models))
        self.assertEqual(code, 0)
        self.assertIn("FOUND: input.png", out)
        self.assertIn(str(nested / "input.png"), out)
        self.assertEqual(err, "")

    def test_missing(self):
        code, out, _ = self.run_cli("--artifact-dir", str(self.models))
        self.assertEqual(code, 1)
        self.assertIn("MISSING: input.png", out)

    def test_hash_mismatch(self):
        (self.models / "input.png").write_bytes(b"bad")
        self.manifest["artifacts"][0]["sha256"] = hashlib.sha256(b"good").hexdigest()
        self.write_manifest()
        code, out, _ = self.run_cli("--artifact-dir", str(self.models))
        self.assertEqual(code, 1)
        self.assertIn("HASH MISMATCH: input.png", out)
        self.assertIn("sha256", out)

    def test_size_mismatch(self):
        (self.models / "input.png").write_bytes(b"bad")
        self.manifest["artifacts"][0]["size_bytes"] = 4
        self.write_manifest()
        code, out, _ = self.run_cli("--artifact-dir", str(self.models))
        self.assertEqual(code, 1)
        self.assertIn("HASH MISMATCH: input.png", out)
        self.assertIn("size 3, expected 4", out)

    def test_size_and_hash_match(self):
        (self.models / "input.png").write_bytes(b"good")
        self.manifest["artifacts"][0].update(size_bytes=4,
            sha256=hashlib.sha256(b"good").hexdigest().upper())
        self.write_manifest()
        self.assertEqual(self.run_cli("--artifact-dir", str(self.models))[0], 0)

    def test_valid_size_does_not_hide_wrong_hash(self):
        (self.models / "input.png").write_bytes(b"evil")
        self.manifest["artifacts"][0].update(size_bytes=4,
            sha256=hashlib.sha256(b"good").hexdigest())
        self.write_manifest()
        self.assertEqual(self.run_cli("--artifact-dir", str(self.models))[0], 1)

    def test_bad_copy_does_not_hide_good_copy(self):
        (self.models / "input.png").write_bytes(b"bad")
        second = self.root / "second"
        second.mkdir()
        (second / "input.png").write_bytes(b"good")
        self.manifest["artifacts"][0]["sha256"] = hashlib.sha256(b"good").hexdigest()
        self.write_manifest()
        code, out, _ = self.run_cli("--artifact-dir", str(self.models),
                                    "--artifact-dir", str(second))
        self.assertEqual(code, 0)
        self.assertIn(str(second / "input.png"), out)
        self.assertIn("rejected:", out)

    def test_deterministic_candidate_order(self):
        for name in ("z", "a"):
            folder = self.models / name
            folder.mkdir()
            (folder / "input.png").write_bytes(b"good")
        result = inspect_artifacts(self.manifest["artifacts"], [self.models])[0]
        self.assertEqual(result["path"], str(self.models / "a" / "input.png"))

    def test_config_relative_paths(self):
        (self.models / "input.png").write_bytes(b"good")
        config = self.root / "local.yaml"
        config.write_text("artifact_dirs:\n  - models\n")
        self.assertEqual(self.run_cli("--config", str(config))[0], 0)

    def test_environment_paths(self):
        (self.models / "input.png").write_bytes(b"good")
        with patch.dict(os.environ, {"RENDERREPO_ARTIFACT_DIRS": str(self.models)}):
            self.assertEqual(self.run_cli()[0], 0)

    def test_cli_overrides_config_and_environment(self):
        (self.models / "input.png").write_bytes(b"good")
        with patch.dict(os.environ, {"RENDERREPO_ARTIFACT_DIRS": "not-a-directory"}):
            self.assertEqual(self.run_cli("--config", str(self.root / "missing.yaml"),
                                         "--artifact-dir", str(self.models))[0], 0)

    def test_no_roots_does_not_search_package(self):
        (self.package / "input.png").write_bytes(b"good")
        with patch.dict(os.environ, {"RENDERREPO_ARTIFACT_DIRS": ""}):
            self.assertEqual(self.run_cli()[0], 1)

    def test_invalid_directory_fails_closed(self):
        code, _, err = self.run_cli("--artifact-dir", str(self.root / "missing"))
        self.assertEqual(code, 2)
        self.assertIn("directory does not exist", err)

    def test_read_error_fails_closed(self):
        (self.models / "input.png").symlink_to(self.root / "missing")
        self.assertEqual(self.run_cli("--artifact-dir", str(self.models))[0], 2)

    def test_invalid_manifest_fields(self):
        for field, value in (("size_bytes", -1), ("size_bytes", True),
                             ("sha256", "not-a-hash"), ("filename", "../input.png"),
                             ("sources", [{"uri": 42}]), ("surprise", True)):
            with self.subTest(field=field, value=value):
                self.manifest["artifacts"] = [{"filename": "input.png", field: value}]
                self.write_manifest()
                self.assertEqual(self.run_cli()[0], 2)

    def test_duplicate_filenames_rejected(self):
        self.manifest["artifacts"] *= 2
        self.write_manifest()
        self.assertEqual(self.run_cli()[0], 2)

    def test_workflow_escape_rejected(self):
        self.manifest["workflow"] = "../outside.json"
        self.write_manifest()
        self.assertEqual(self.run_cli()[0], 2)

    def test_invalid_workflow_rejected(self):
        for data in ("not json", "{}", "[]", '{"nodes": null}'):
            with self.subTest(data=data):
                (self.package / "workflow.json").write_text(data)
                self.assertEqual(self.run_cli()[0], 2)

    def test_manifest_file_argument(self):
        (self.models / "input.png").write_bytes(b"good")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["inspect", str(self.package / "manifest.yaml"),
                                   "--artifact-dir", str(self.models)]), 0)

    def test_real_wan_workflow_acceptance(self):
        manifest = load_package(EXAMPLE)
        self.assertEqual(len(manifest["artifacts"]), 7)
        workflow_bytes = (EXAMPLE / "workflow.json").read_bytes()
        graph = json.loads(workflow_bytes)
        # Compare inventory with the actual exposed widgets, including input media.
        expected = set()
        for node in graph["nodes"]:
            values = node.get("widgets_values_named", {})
            for key in ("image", "unet_name", "unet_name_1", "lora_name",
                        "lora_name_1", "clip_name", "vae_name"):
                if key in values:
                    expected.add(values[key])
        self.assertEqual({a["filename"] for a in manifest["artifacts"]}, expected)
        self.assertTrue(all("sha256" in a for a in manifest["artifacts"][:-1]))
        self.assertTrue(all(a.get("sources") for a in manifest["artifacts"]))
        empty = inspect_artifacts(manifest["artifacts"], [self.models])
        self.assertTrue(all(r["status"] == "MISSING" for r in empty))
        # Use the real graph and filenames with tiny stand-in checksums; the real
        # published model hashes must never accept these placeholders.
        for artifact in manifest["artifacts"]:
            (self.models / artifact["filename"]).write_bytes(b"test placeholder")
        rejected = inspect_artifacts(manifest["artifacts"], [self.models])
        self.assertTrue(all(r["status"] == "HASH MISMATCH" for r in rejected[:-1]))
        reference = self.root / "wan-reference"
        reference.mkdir()
        (reference / "workflow.json").write_bytes(workflow_bytes)
        for artifact in manifest["artifacts"]:
            artifact["size_bytes"] = len(b"test placeholder")
            artifact["sha256"] = hashlib.sha256(b"test placeholder").hexdigest()
        (reference / "manifest.yaml").write_text(yaml.safe_dump(manifest))
        args = [sys.executable, "-m", "renderrepo", "inspect", str(reference),
                "--artifact-dir", str(self.models)]
        passed = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertEqual(passed.stdout.count("FOUND:"), 7)
        image = self.models / "video_wan2_2_14B_i2v_input_image.jpg"
        image.unlink()
        missing = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(missing.returncode, 1)
        self.assertIn("MISSING: " + image.name, missing.stdout)
        self.assertEqual((EXAMPLE / "workflow.json").read_bytes(), workflow_bytes)


if __name__ == "__main__":
    unittest.main()
