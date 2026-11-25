import hashlib
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile, ZipInfo


COMMAND = Path(__file__).resolve().parents[1] / "candidate_set.py"
TARGETS = {
    "macos": ("arm64", "SyncWatch.app/Contents/MacOS/SyncWatch"),
    "windows": ("x64", "SyncWatch/SyncWatch.exe"),
    "linux": ("x64", "SyncWatch/SyncWatch"),
}
SOURCE = {"sourceCommit": "a" * 40, "sourceHead": "b" * 40, "sourceTree": "c" * 40, "runId": "1234"}


def json_bytes(value):
    return json.dumps(value, sort_keys=True).encode()


class CandidateSetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="candidate set ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.archives = {}

    def write_archive(self, platform, *, manifest_changes=None, executable=b"fixture application", can_launch=True):
        architecture, launcher = TARGETS[platform]
        directory_names = tuple(parent.as_posix() for parent in Path(launcher).parents if parent.name)
        expected_executable = b"fixture application"
        receipt = json_bytes({
            "schemaVersion": 1,
            "platform": f"{platform}-{architecture}",
            "sourceCommit": SOURCE["sourceCommit"],
            "sourceInputs": {"commit": SOURCE["sourceCommit"], "localChanges": False},
            "developmentCandidate": True,
            "releaseReady": False,
            "files": {name: {"kind": "directory"} for name in directory_names} | {
                launcher: {"kind": "file", "sha256": hashlib.sha256(expected_executable).hexdigest(),
                           "bytes": len(expected_executable), "executable": can_launch}
            },
        })
        validation = b"<testsuite/>"
        manifest = json_bytes({
            **SOURCE, "schemaVersion": 1, "platform": platform, "architecture": architecture,
            "runAttempt": "2" if platform == "linux" else "1", "applicationBuilt": True,
            "validation": "success", "validationScope": "native-build-and-standalone-package",
            "candidateReceiptSha256": hashlib.sha256(receipt).hexdigest(),
            "files": {"validation.xml": hashlib.sha256(validation).hexdigest()},
        } | (manifest_changes or {}))
        path = self.root / f"{platform}.zip"
        with ZipFile(path, "w") as package:
            package.writestr("build-manifest.json", manifest)
            package.writestr("validation.xml", validation)
            package.writestr("candidate/candidate.json", receipt)
            for directory in directory_names:
                item = ZipInfo(f"candidate/contents/{directory}/")
                item.external_attr = (stat.S_IFDIR | 0o755) << 16
                package.writestr(item, b"")
            item = ZipInfo(f"candidate/contents/{launcher}")
            item.external_attr = (stat.S_IFREG | (0o755 if can_launch else 0o644)) << 16
            package.writestr(item, executable)
        self.archives[platform] = {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def run_check(self):
        request = self.root / "request.json"
        request.write_bytes(json_bytes(SOURCE | {"archives": self.archives}))
        return subprocess.run([sys.executable, "-B", str(COMMAND), str(request)], cwd=self.root,
                              capture_output=True, text=True, timeout=15)

    def test_reports_three_packages_from_reviewed_tree(self):
        for platform in TARGETS:
            self.write_archive(platform)
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stderr)
        evidence = json.loads(result.stdout)
        self.assertEqual(evidence["sourceTree"], SOURCE["sourceTree"])
        self.assertEqual(evidence["runId"], SOURCE["runId"])
        self.assertEqual(set(evidence["archives"]), set(TARGETS))
        self.assertEqual(evidence["archives"]["linux"]["runAttempt"], "2")
        self.assertEqual(evidence["archives"]["macos"]["sha256"], self.archives["macos"]["sha256"])
        self.assertIs(evidence["releaseReady"], False)
        self.assertNotIn(str(self.root), result.stdout)

    def test_rejects_linux_package_with_non_executable_launcher(self):
        for platform in TARGETS:
            self.write_archive(platform, can_launch=platform != "linux")
        result = self.run_check()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("launcher is not executable", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_rejects_mixed_source_tree_or_run(self):
        for platform in TARGETS:
            self.write_archive(platform)
        for changes in ({"sourceTree": "d" * 40}, {"sourceCommit": "d" * 40}, {"runId": "4321"}):
            with self.subTest(changes=changes):
                self.write_archive("windows", manifest_changes=changes)
                result = self.run_check()
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("different source, run, target, or validation result", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_rejects_package_that_differs_from_published_digest(self):
        for platform in TARGETS:
            self.write_archive(platform)
        self.archives["linux"]["sha256"] = "e" * 64
        result = self.run_check()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("Archive SHA-256 differs", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_rejects_changed_payload_under_an_independently_pinned_archive_hash(self):
        for platform in TARGETS:
            payload = b"mutated application" if platform == "macos" else b"fixture application"
            self.write_archive(platform, executable=payload)
        result = self.run_check()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("Archive member digest differs", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_rejects_incomplete_platform_set(self):
        self.write_archive("macos")
        self.write_archive("windows")
        result = self.run_check()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("Expected one archive each", result.stderr)
        self.assertEqual(result.stdout, "")
