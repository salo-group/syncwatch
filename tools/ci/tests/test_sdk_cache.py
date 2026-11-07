import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sdk_cache


class SdkCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bytes = b"pinned developer archive"
        self.item = {"url": "https://example.invalid/vendor.zip",
                     "sha256": hashlib.sha256(self.bytes).hexdigest()}
        self.write("tools/jvm/toolchains.json", {"targets": {"linux-x64": self.item}})
        self.write("tools/jvm/kotlin_toolchain.json", {"archive": self.item, "sbom": self.item})
        self.write("tools/vlc/windows-sdk.json", {"base": self.item,
                   "archives": {f"package-{i}.pkg.tar.zst": self.item for i in range(12)}})

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def plan(self, *sdks, target="linux-x64"):
        return sdk_cache.plan(self.root, target, sdks)

    def materialize(self, plan, **options):
        # Each download receives its own stream, including concurrent calls.
        with patch("sdk_cache.urllib.request.urlopen", side_effect=lambda *a, **k: io.BytesIO(self.bytes)):
            sdk_cache.materialize(self.root, plan, **options)

    def test_cache_identity_tracks_pins_but_not_unrelated_workflow_edits(self):
        first = self.plan("jdk")["groups"][0]["key"]
        self.write(".github/workflows/repository.yml", {"unrelated": "change"})
        self.assertEqual(self.plan("jdk")["groups"][0]["key"], first)
        self.write("tools/jvm/toolchains.json", {"targets": {"linux-x64": {**self.item, "sha256": "a" * 64}}})
        self.assertNotEqual(self.plan("jdk")["groups"][0]["key"], first)
        with self.assertRaises(ValueError):
            self.plan("jdk", target="unknown")

    def test_restored_archives_supply_exact_vendor_bytes_without_network(self):
        plan = self.plan("jdk")
        self.materialize(plan)
        entry = plan["groups"][0]["entries"][0]
        destination = self.root / entry["destination"]
        destination.unlink()
        with patch("sdk_cache.urllib.request.urlopen", side_effect=AssertionError("unexpected network")):
            sdk_cache.materialize(self.root, plan)
        self.assertEqual(destination.read_bytes(), self.bytes)

    def test_cold_build_ignores_a_restored_cache(self):
        plan = self.plan("jdk")
        archive = self.root / plan["groups"][0]["archive"]
        archive.parent.mkdir(parents=True)
        archive.write_bytes(b"invalid cache that must not be read")
        self.materialize(plan, cold=True)
        self.assertTrue(zipfile.is_zipfile(archive))

    def test_corrupt_unexpected_and_traversing_cache_entries_fail_before_publication(self):
        plan = self.plan("jdk")
        group = plan["groups"][0]
        archive = self.root / group["archive"]
        archive.parent.mkdir(parents=True)
        entry = group["entries"][0]
        for name, data in ((entry["name"], b"corrupt"), ("../escape", self.bytes), ("extra", self.bytes)):
            with self.subTest(name=name), zipfile.ZipFile(archive, "w") as package:
                package.writestr(name, data)
            with self.assertRaises(ValueError), patch("sdk_cache.urllib.request.urlopen") as network:
                sdk_cache.materialize(self.root, plan)
            network.assert_not_called()
            self.assertFalse((self.root / entry["destination"]).exists())

    def test_failed_vendor_download_cannot_publish_partial_or_cached_bytes(self):
        plan = self.plan("jdk")
        with patch("sdk_cache.urllib.request.urlopen", return_value=io.BytesIO(b"corrupt")):
            with self.assertRaises(ValueError):
                sdk_cache.materialize(self.root, plan)
        group = plan["groups"][0]
        self.assertFalse((self.root / group["archive"]).exists())
        self.assertFalse((self.root / group["entries"][0]["destination"]).exists())

    def test_windows_downloads_overlap_and_remain_bounded(self):
        plan = self.plan("windows-vlc", target="windows-x64")
        barrier = threading.Barrier(4)
        lock = threading.Lock()
        active, maximum = 0, 0

        def download(*args, **kwargs):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            try:
                # The first batch must overlap; a serial implementation fails.
                if maximum < 4:
                    barrier.wait(timeout=5)
                else:
                    try:
                        barrier.wait(timeout=0.05)
                    except threading.BrokenBarrierError:
                        pass
                return io.BytesIO(self.bytes)
            finally:
                with lock:
                    active -= 1

        with patch("sdk_cache.urllib.request.urlopen", side_effect=download):
            sdk_cache.materialize(self.root, plan)
        self.assertEqual(maximum, 4)
        self.assertEqual(sum(len(g["entries"]) for g in plan["groups"]), 13)

    def test_schedule_manual_and_environment_cold_requests_are_preserved(self):
        event = self.root / "event.json"
        event.write_text('{"inputs":{"cold":"true"}}')
        for environment in ({"GITHUB_EVENT_NAME": "schedule"}, {"CI_COLD": "true"},
                            {"GITHUB_EVENT_PATH": str(event)}):
            with patch.dict(os.environ, environment, clear=True):
                self.assertTrue(sdk_cache.cold_requested())
        with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "pull_request"}, clear=True):
            self.assertFalse(sdk_cache.cold_requested())


if __name__ == "__main__":
    unittest.main()
