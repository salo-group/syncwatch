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
REQUIRED_FILES = {
    "macos": ("SyncWatch.app/Contents/Info.plist",),
    "windows": ("SyncWatch/SyncWatch.dll", "SyncWatch/SyncWatch.deps.json", "SyncWatch/SyncWatch.runtimeconfig.json"),
    "linux": ("SyncWatch/launch.args", "SyncWatch/Runtime/bin/java"),
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

    def write_archive(self, platform, *, manifest_changes=None, executable=b"fixture application",
                      can_launch=True, omit=None):
        architecture, launcher = TARGETS[platform]
        payload = {
            name: (b"fixture metadata", name.endswith("/bin/java"))
            for name in REQUIRED_FILES[platform] if name != omit
        }
        payload[launcher] = (b"fixture application", can_launch)
        directory_names = {
            parent.as_posix() for name in payload for parent in Path(name).parents if parent.name
        }
        files = {name: {"kind": "directory"} for name in directory_names}
        files.update({
            name: {"kind": "file", "sha256": hashlib.sha256(data).hexdigest(),
                   "bytes": len(data), "executable": executable}
            for name, (data, executable) in payload.items()
        })
        component = launcher.split("/")[0]
        receipt = json_bytes({
            "schemaVersion": 1,
            "platform": f"{platform}-{architecture}",
            "sourceCommit": SOURCE["sourceCommit"],
            "sourceInputs": {"commit": SOURCE["sourceCommit"], "localChanges": False},
            "developmentCandidate": True,
            "releaseReady": False,
            "components": {component: {name.split("/", 1)[1]: entry for name, entry in files.items() if name != component}},
            "files": files,
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
            for name, (data, mode) in payload.items():
                item = ZipInfo(f"candidate/contents/{name}")
                item.external_attr = (stat.S_IFREG | (0o755 if mode else 0o644)) << 16
                package.writestr(item, executable if name == launcher else data)
        self.archives[platform] = {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def run_check(self, *, request_exists=True):
        request = self.root / "request.json"
        if request_exists:
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


    def test_rejects_missing_platform_launch_inputs(self):
        for platform in TARGETS:
            self.write_archive(platform)
        for platform in TARGETS:
            with self.subTest(platform=platform):
                self.write_archive(platform, omit=REQUIRED_FILES[platform][0])
                result = self.run_check()
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("Candidate launch input is missing", result.stderr)
                self.assertEqual(result.stdout, "")
                self.write_archive(platform)

    def test_missing_request_error_omits_local_path(self):
        result = self.run_check(request_exists=False)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertNotIn(str(self.root), result.stderr)
