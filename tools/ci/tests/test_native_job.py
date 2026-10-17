import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_job


class NativeJobChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def initialize_repository(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / "source.txt").write_text("source\n")
        subprocess.run(["git", "add", "source.txt"], cwd=self.root, check=True)
        subprocess.run(
            ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "Fixture"],
            cwd=self.root, check=True,
        )

    def report(self):
        report = native_job.Report()
        report.execute("Native checks", lambda: None)
        return report

    def test_failed_stage_has_a_real_failing_xml_result(self):
        report = native_job.Report()
        with self.assertRaises(RuntimeError):
            report.execute("Build", lambda: (_ for _ in ()).throw(RuntimeError()))
        path = self.root / "validation.xml"
        report.write(path)
        suite = ET.parse(path).getroot()
        self.assertEqual(suite.attrib["tests"], "1")
        self.assertEqual(suite.attrib["failures"], "1")
        self.assertIsNotNone(suite.find("testcase/failure"))

    def test_wrong_runner_architecture_is_rejected(self):
        with patch("native_job.platform.system", return_value="Darwin"), patch("native_job.platform.machine", return_value="x86_64"):
            with self.assertRaisesRegex(ValueError, "architecture"):
                native_job.check_host("apple")

    def test_sdk_archive_cannot_escape_its_destination(self):
        for member in ("../outside", "/absolute", "C:/outside", "nested\\outside"):
            with self.subTest(member=member):
                archive = self.root / "sdk.zip"
                with zipfile.ZipFile(archive, "w") as package:
                    package.writestr(member, "bad")
                with self.assertRaisesRegex(ValueError, "unsafe path"):
                    native_job.extract_sdk(archive, self.root / "sdk")

    def test_sdk_archive_symlinks_are_rejected(self):
        archive = self.root / "sdk.zip"
        with zipfile.ZipFile(archive, "w") as package:
            entry = zipfile.ZipInfo("link")
            entry.create_system = 3
            entry.external_attr = 0o120777 << 16
            package.writestr(entry, "../outside")
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            native_job.extract_sdk(archive, self.root / "sdk")

    def test_valid_sdk_archive_extracts_local_files(self):
        archive = self.root / "sdk.zip"
        with zipfile.ZipFile(archive, "w") as package:
            package.writestr("sdk/version.txt", "10.0.402")
        native_job.extract_sdk(archive, self.root / "sdk")
        self.assertEqual((self.root / "sdk/sdk/version.txt").read_text(), "10.0.402")

    def test_sdk_checksum_is_verified_before_extraction(self):
        specification = {
            "dotnetSdk": "10.0.402", "sdkArchive": "https://builds.dotnet.microsoft.com/sdk.zip",
            "sdkArchiveSha512": "0" * 128,
        }
        with patch("native_job.shutil.which", return_value=None), patch("native_job.urllib.request.urlopen", return_value=io.BytesIO(b"unexpected")):
            with patch("native_job.extract_sdk") as extract:
                with self.assertRaisesRegex(ValueError, "checksum"):
                    native_job.ensure_dotnet(specification, self.root, self.root)
                extract.assert_not_called()

    def test_sdk_provisioning_rejects_nonofficial_download_sources(self):
        specification = {"dotnetSdk": "10.0.402", "sdkArchive": "https://example.invalid/sdk.zip"}
        with patch("native_job.shutil.which", return_value=None), self.assertRaisesRegex(ValueError, "official HTTPS"):
            native_job.ensure_dotnet(specification, self.root, self.root)

    def test_candidate_archive_identifies_exact_source_and_payload_digests(self):
        self.initialize_repository()
        bundle = self.root / "artifacts/apple/SyncWatch.app/Contents/MacOS"
        bundle.mkdir(parents=True)
        (bundle / "SyncWatch").write_bytes(b"executable fixture")
        (bundle / "SyncWatch").chmod(0o755)
        directory = self.root / "artifacts/native/apple"
        output = native_job.prepare_archive(self.root, directory, "apple", "arm64", {"swift": "6.4"}, self.report(), True)
        with zipfile.ZipFile(output) as archive:
            manifest = json.loads(archive.read("build-manifest.json"))
            expected = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.root, text=True).strip()
            self.assertEqual(manifest["sourceCommit"], expected)
            self.assertEqual(manifest["architecture"], "arm64")
            self.assertEqual(manifest["validation"], "success")
            for name, digest in manifest["files"].items():
                self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), digest)
            with tarfile.open(fileobj=io.BytesIO(archive.read("SyncWatch.app.tar"))) as bundle_archive:
                executable = bundle_archive.getmember("SyncWatch.app/Contents/MacOS/SyncWatch")
                self.assertEqual(executable.mode & 0o111, 0o111)
                self.assertEqual(executable.uid, 0)
                self.assertEqual(executable.uname, "")

    def test_failure_archive_contains_reports_without_an_application(self):
        self.initialize_repository()
        output = native_job.prepare_archive(self.root, self.root / "output", "windows", "x64", {}, self.report(), False)
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(set(archive.namelist()), {"validation.xml", "build-manifest.json"})
            self.assertEqual(json.loads(archive.read("build-manifest.json"))["validation"], "failure")

    def test_bundle_symlinks_cannot_include_external_files(self):
        bundle = self.root / "artifacts/apple/SyncWatch.app/Contents/MacOS"
        bundle.mkdir(parents=True)
        (bundle / "SyncWatch").write_bytes(b"executable")
        (self.root / "external").write_text("private")
        (bundle / "outside").symlink_to(self.root / "external")
        with self.assertRaisesRegex(ValueError, "symbolic links"):
            native_job.apple_candidate(self.root, self.root / "candidate.tar")


if __name__ == "__main__":
    unittest.main()
