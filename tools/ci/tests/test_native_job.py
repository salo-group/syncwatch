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

    def report(self, with_service=False):
        report = native_job.Report()
        report.execute("Native checks", lambda: None)
        if with_service:
            report.execute(native_job.WINDOWS_SERVICE_STAGE, lambda: None)
        return report

    def test_windows_success_cannot_be_published_without_successful_service_checks(self):
        for service_result in (None, False):
            with self.subTest(service_result=service_result):
                report = self.report()
                if service_result is not None:
                    report.cases.append({"name": native_job.WINDOWS_SERVICE_STAGE, "success": service_result, "seconds": 0})
                directory = self.root / "artifacts/native/windows"
                with self.assertRaisesRegex(ValueError, "admitted room service stage"):
                    native_job.prepare_archive(self.root, directory, "windows", "x64", {}, report, True)
                self.assertFalse((directory / "payload").exists())

    def design_matrix(self, target="macos"):
        source = self.root / "artifacts/design" / target
        source.mkdir(parents=True)
        repository = Path(__file__).resolve().parents[3]
        for relative in ("packages/design/components.json", "packages/design/tokens/geometry.json"):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((repository / relative).read_bytes())
        for name in native_job.design_image_names(self.root):
            (source / name).write_bytes(b"\x89PNG\r\n\x1a\nfixture")
        return source

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
                native_job.check_host("macos")

    def candidate(self, target):
        sys.path.insert(0, str(native_job.ROOT / "tools/application"))
        from candidate_tree import inventory
        destination = self.root / "artifacts/application-ci" / target / "candidate"
        contents = destination / "contents/SyncWatch"
        contents.mkdir(parents=True)
        (contents / "SyncWatch").write_bytes(b"native executable fixture")
        (contents / "SyncWatch").chmod(0o755)
        (contents / "Runtime").mkdir()
        (contents / "Runtime/empty").mkdir()
        (contents / "Runtime/engine").write_bytes(b"private runtime fixture")
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.root, text=True).strip()
        (destination / "candidate.json").write_text(json.dumps({
            "schemaVersion": 1, "platform": target, "sourceCommit": commit,
            "developmentCandidate": True, "releaseReady": False,
            "sourceInputs": {"commit": commit, "localChanges": False},
            "files": inventory(destination / "contents"),
        }))
        return destination

    def test_linux_package_has_a_complete_application_without_a_gui_claim(self):
        self.initialize_repository()
        self.candidate("linux-x64")
        source = self.root / "artifacts/linux-boundary/report.json"
        source.parent.mkdir(parents=True)
        source.write_text('{"jvmBoundaryAccepted":true,"applicationBuilt":false,"guiAccepted":false}\n')
        directory = self.root / "artifacts/native/linux"
        archive = native_job.prepare_archive(self.root, directory, "linux", "x64", {"javac": "pinned"}, self.report(), True)
        with zipfile.ZipFile(archive) as package:
            manifest = json.loads(package.read("build-manifest.json"))
            self.assertEqual(manifest["validation"], "success")
            self.assertEqual(manifest["validationScope"], "native-build-and-standalone-package")
            self.assertTrue(manifest["applicationBuilt"])
            self.assertFalse(manifest["guiAccepted"])
            self.assertFalse(manifest["physicalOutputAccepted"])
            self.assertEqual(manifest["candidateReceiptSha256"], hashlib.sha256(package.read("candidate/candidate.json")).hexdigest())
            self.assertEqual(package.read("candidate/contents/SyncWatch/Runtime/engine"), b"private runtime fixture")
            self.assertIn("linux-boundary.json", package.namelist())
            self.assertTrue(package.getinfo("candidate/contents/SyncWatch/Runtime/empty/").is_dir())
            relocated = self.root / "relocated"
            package.extractall(relocated)
            for entry in package.infolist():
                if not entry.is_dir():
                    (relocated / entry.filename).chmod((entry.external_attr >> 16) & 0o777)
            from candidate_tree import inventory
            receipt = json.loads(package.read("candidate/candidate.json"))
            self.assertEqual(inventory(relocated / "candidate/contents"), receipt["files"])

    def test_boundary_checks_without_a_package_cannot_publish_success(self):
        self.initialize_repository()
        with self.assertRaises(FileNotFoundError):
            native_job.prepare_archive(self.root, self.root / "report", "linux", "x64", {}, self.report(), True)

    def test_linux_host_is_required_for_linux_lane(self):
        with patch("native_job.platform.system", return_value="Linux"), patch("native_job.platform.machine", return_value="x86_64"):
            self.assertEqual(native_job.check_host("linux"), "x64")
        with patch("native_job.platform.system", return_value="Windows"), patch("native_job.platform.machine", return_value="AMD64"):
            with self.assertRaisesRegex(ValueError, "architecture"):
                native_job.check_host("linux")

    def test_sdk_archive_cannot_escape_its_destination(self):
        for member in ("../outside", "/absolute", "C:/outside", "nested\\outside", "nested\x00outside"):
            with self.subTest(member=member):
                archive = self.root / "sdk.zip"
                with zipfile.ZipFile(archive, "w") as package:
                    entry = zipfile.ZipInfo("fixture")
                    entry.filename = member
                    package.writestr(entry, "bad")
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
        candidate = self.candidate("macos-arm64")
        executable = candidate / "contents/SyncWatch/SyncWatch"
        self.design_matrix()
        directory = self.root / "artifacts/native/macos"
        output = native_job.prepare_archive(self.root, directory, "macos", "arm64", {"swift": "6.4"}, self.report(), True)
        with zipfile.ZipFile(output) as archive:
            manifest = json.loads(archive.read("build-manifest.json"))
            expected = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.root, text=True).strip()
            self.assertEqual(manifest["sourceCommit"], expected)
            self.assertEqual(manifest["architecture"], "arm64")
            self.assertEqual(manifest["validation"], "success")
            for name, digest in manifest["files"].items():
                self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), digest)
            entry = archive.getinfo("candidate/contents/SyncWatch/SyncWatch")
            self.assertEqual((entry.external_attr >> 16) & 0o111, executable.stat().st_mode & 0o111)
            self.assertEqual(entry.date_time, (1980, 1, 1, 0, 0, 0))
            if os.name != "nt":
                self.assertEqual((entry.external_attr >> 16) & 0o111, 0o111)

    def test_design_images_retain_digests_and_reject_external_links(self):
        source = self.design_matrix()
        payload = self.root / "payload"
        payload.mkdir()
        native_job.retain_design_images(self.root, payload, "macos")
        self.assertEqual(len(list((payload / "design").iterdir())), 352)
        image = next(source.iterdir())
        image.unlink()
        image.symlink_to(self.root / "source.txt")
        (self.root / "source.txt").write_text("private")
        (self.root / "rejected").mkdir()
        with self.assertRaisesRegex(ValueError, "Unsafe design"):
            native_job.retain_design_images(self.root, self.root / "rejected", "macos")

    def test_render_matrix_rejects_missing_and_stale_names(self):
        source = self.design_matrix()
        image = next(source.iterdir())
        wrong = source / "stale-dark-en-680x420.png"
        image.rename(wrong)
        with self.assertRaisesRegex(ValueError, "differs"):
            native_job.retain_design_images(self.root, self.root / "out", "macos")
        wrong.unlink()
        with self.assertRaisesRegex(ValueError, "differs"):
            native_job.retain_design_images(self.root, self.root / "out", "macos")

    def test_missing_entire_matrix_cannot_publish_success(self):
        with self.assertRaisesRegex(ValueError, "Required.*missing"):
            native_job.retain_design_images(self.root, self.root / "payload", "macos")

    def test_failure_archive_contains_reports_without_an_application(self):
        self.initialize_repository()
        output = native_job.prepare_archive(self.root, self.root / "output", "windows", "x64", {}, self.report(), False)
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(set(archive.namelist()), {"validation.xml", "build-manifest.json"})
            self.assertEqual(json.loads(archive.read("build-manifest.json"))["validation"], "failure")

    def test_stale_or_changed_package_cannot_publish_success(self):
        self.initialize_repository()
        candidate = self.candidate("windows-x64")
        path = candidate / "contents/SyncWatch/Runtime/engine"
        path.write_text("changed after receipt")
        with self.assertRaisesRegex(ValueError, "stale, incomplete"):
            native_job.prepare_archive(self.root, self.root / "report", "windows", "x64", {}, self.report(with_service=True), True)

    def test_archive_mutation_never_publishes_a_partial_success(self):
        self.initialize_repository()
        candidate = self.candidate("windows-x64")
        self.design_matrix("windows")
        original = native_job.sha256
        def mutate(path):
            digest = original(path)
            if path.name == "candidate.json":
                (candidate / "contents/SyncWatch/Runtime/engine").write_text("changed during publication")
            return digest
        output = self.root / "report"
        with patch("native_job.sha256", side_effect=mutate), self.assertRaisesRegex(ValueError, "changed while archiving"):
            native_job.prepare_archive(self.root, output, "windows", "x64", {}, self.report(with_service=True), True)
        self.assertFalse((output / "artifact.zip").exists())
        self.assertFalse(list(output.glob(".native-archive-*")))

    def test_package_symlinks_cannot_include_external_files(self):
        self.initialize_repository()
        candidate = self.candidate("windows-x64")
        (self.root / "external").write_text("private")
        (candidate / "contents/SyncWatch/outside").symlink_to(self.root / "external")
        with self.assertRaises(ValueError):
            native_job.prepare_archive(self.root, self.root / "report", "windows", "x64", {}, self.report(with_service=True), True)


if __name__ == "__main__":
    unittest.main()
