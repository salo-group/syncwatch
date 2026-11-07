import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import linux_runtime_cache as cache


class LinuxRuntimeCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.directory = self.root / "artifacts/application-ci/linux-x64"
        self.directory.mkdir(parents=True)
        dependencies, receipts = cache.modules()

        class Validators:
            jvm = staticmethod(lambda path: receipts.checked_receipt(path, "jvm-report.json"))
            clients = staticmethod(lambda path: receipts.checked_receipt(path, "clients-report.json"))

        self.inputs = {"mediaReceiptSha256": "a" * 64, "compiler": "original compiler", "sources": "pinned originals"}
        for target, attribute, value in ((cache, "ROOT", self.root),
                                          (cache, "modules", lambda: (dependencies, Validators)),
                                          (cache, "snapshot", lambda context: copy.deepcopy(self.inputs)),
                                          (cache, "cold_requested", lambda: False)):
            patcher = patch.object(target, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                        "commit", "--allow-empty", "-qm", "Runtime fixture"], cwd=self.root, check=True)
        self.request = cache.prepare(self.directory, {})
        self.path = cache.request_path(self.directory)

    def payloads(self):
        for name, filename in (("jvm", "jvm-report.json"), ("clients", "clients-report.json")):
            payload = self.directory / name / "payload"
            payload.mkdir(parents=True)
            executable = payload / "engine"
            executable.write_bytes(b"source-built fixture executable")
            executable.chmod(0o755)
            (payload / filename).write_text(json.dumps({
                "schemaVersion": 1, "mediaReceiptSha256": self.inputs["mediaReceiptSha256"],
                "files": {"engine": hashlib.sha256(executable.read_bytes()).hexdigest()},
            }))

    def clear_outputs(self):
        for name in ("jvm", "clients"):
            shutil.rmtree(self.directory / name)

    def test_cold_build_outputs_round_trip_and_receive_fresh_receipt_validation(self):
        self.assertFalse(cache.restore(self.path))
        self.payloads()
        cache.stage(self.path)
        self.clear_outputs()
        self.assertTrue(cache.restore(self.path))
        for name in ("jvm", "clients"):
            executable = self.directory / name / "payload/engine"
            self.assertEqual(executable.read_bytes(), b"source-built fixture executable")
            self.assertTrue(executable.stat().st_mode & 0o111)
        restored = json.loads(self.path.read_text())
        self.assertTrue(restored["cacheHit"])
        self.assertRegex(restored["buildSourceCommit"], r"^[0-9a-f]{40}$")

    def test_changed_compiler_and_source_inputs_cannot_reuse_old_outputs(self):
        self.payloads()
        cache.stage(self.path)
        old_archive = Path(self.request["archive"])
        self.clear_outputs()
        self.inputs["compiler"] = "different compiler"
        with self.assertRaisesRegex(ValueError, "inputs changed"):
            cache.restore(self.path)
        updated = cache.prepare(self.directory, {})
        self.assertNotEqual(updated["key"], self.request["key"])
        shutil.copyfile(old_archive, updated["archive"])
        with self.assertRaisesRegex(ValueError, "inputs do not match"):
            cache.restore(self.path)
        self.assertFalse((self.directory / "jvm").exists())

    def test_changed_payload_cannot_be_cached_under_a_valid_receipt(self):
        self.payloads()
        (self.directory / "jvm/payload/engine").write_bytes(b"unexpected bytes")
        with self.assertRaisesRegex(ValueError, "actual package bytes"):
            cache.stage(self.path)
        self.assertFalse(Path(self.request["archive"]).exists())

    def test_cold_request_ignores_existing_runtime_archive(self):
        self.payloads()
        cache.stage(self.path)
        self.clear_outputs()
        request = json.loads(self.path.read_text())
        request["cold"] = True
        self.path.write_text(json.dumps(request))
        with patch.object(cache, "verify", side_effect=AssertionError("a cold build cannot restore")):
            self.assertFalse(cache.restore(self.path))


if __name__ == "__main__":
    unittest.main()
