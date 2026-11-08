import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
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
        self.original_smoke = cache.smoke
        for target, attribute, value in ((cache, "ROOT", self.root),
                                          (cache, "modules", lambda: (dependencies, Validators)),
                                          (cache, "snapshot", lambda context: copy.deepcopy(self.inputs)),
                                          (cache, "smoke", lambda payload, request: None),
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
        with patch.object(cache, "smoke") as smoke:
            self.assertTrue(cache.restore(self.path))
            smoke.assert_called_once()
        for name in ("jvm", "clients"):
            executable = self.directory / name / "payload/engine"
            self.assertEqual(executable.read_bytes(), b"source-built fixture executable")
            if os.name == "posix":
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

    @unittest.skipUnless(os.name == "posix", "Exact POSIX permission bits require a POSIX filesystem")
    def test_runtime_cache_preserves_empty_directories_and_exact_file_permissions(self):
        self.payloads()
        original = self.directory / "jvm/payload"
        (original / "empty/configuration").mkdir(parents=True)
        (original / "empty/configuration").chmod(0o700)
        (original / "engine").chmod(0o750)
        cache.stage(self.path)
        self.clear_outputs()
        self.assertTrue(cache.restore(self.path))
        restored = self.directory / "jvm/payload"
        self.assertTrue((restored / "empty/configuration").is_dir())
        self.assertEqual((restored / "empty/configuration").stat().st_mode & 0o777, 0o700)
        self.assertEqual((restored / "engine").stat().st_mode & 0o777, 0o750)

    def test_unsafe_or_privileged_cached_layout_cannot_publish_outputs(self):
        self.payloads()
        cache.stage(self.path)
        self.clear_outputs()
        archive = Path(self.request["archive"])
        with zipfile.ZipFile(archive) as package:
            entries = [(entry, package.read(entry)) for entry in package.infolist()]
        for name, mode in (("jvm/../../escape", 0o755), ("jvm", 0o4755)):
            with self.subTest(name=name), zipfile.ZipFile(archive, "w") as package:
                for entry, data in entries:
                    if entry.filename == "build-manifest.json":
                        manifest = json.loads(data)
                        manifest["layout"][name] = {"kind": "directory", "mode": mode}
                        data = json.dumps(manifest).encode()
                    package.writestr(entry, data)
            with self.assertRaises(ValueError):
                cache.restore(self.path)
            self.assertFalse((self.directory / "jvm").exists())
            self.assertFalse((self.directory / "escape").exists())

    def test_cold_request_ignores_existing_runtime_archive(self):
        self.payloads()
        cache.stage(self.path)
        self.clear_outputs()
        request = json.loads(self.path.read_text())
        request["cold"] = True
        self.path.write_text(json.dumps(request))
        with patch.object(cache, "verify", side_effect=AssertionError("a cold build cannot restore")):
            self.assertFalse(cache.restore(self.path))

    def test_fresh_cache_archive_is_exercised_without_replacing_build_outputs(self):
        self.payloads()
        cache.stage(self.path)
        before = (self.directory / "jvm/payload/engine").read_bytes()
        with patch.object(cache, "smoke") as smoke:
            cache.validate_staged(self.path)
            smoke.assert_called_once()
        self.assertEqual((self.directory / "jvm/payload/engine").read_bytes(), before)
        self.assertFalse(json.loads(self.path.read_text()).get("cacheHit", False))

    def test_cached_jvm_smoke_executes_without_service_tokens_or_ambient_java_options(self):
        payload = self.directory / "payload"
        with patch.dict(os.environ, {"ACTIONS_RUNTIME_TOKEN": "secret", "GITHUB_TOKEN": "secret",
                                     "JAVA_TOOL_OPTIONS": "-javaagent:unexpected.jar"}):
            with patch("linux_runtime_cache.subprocess.run") as command:
                self.original_smoke(payload, {"context": {"media": str(self.root / "media")}})
        arguments, options = command.call_args
        self.assertEqual(arguments[0], [str(payload / "jvm/bin/java"), "--list-modules"])
        self.assertTrue(options["check"])
        for name in ("ACTIONS_RUNTIME_TOKEN", "GITHUB_TOKEN", "JAVA_TOOL_OPTIONS"):
            self.assertNotIn(name, options["env"])


if __name__ == "__main__":
    unittest.main()
