"""Reject substituted UI bytecode, changed generation and incomplete legal inputs."""

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import compose_ui
from build_support import digest, jar_bytes


class ComposeUiArtifactTests(unittest.TestCase):
    def test_admission_fixture_is_recorded_as_current_consumer_input(self):
        registry = json.loads((compose_ui.ROOT / "tools/dependencies/components.json").read_text())
        self.assertIn("tools/consumers/admission_fixture.py",
                      registry["components"][compose_ui.COMPONENT]["verificationInputs"])

    @unittest.skipUnless(sys.platform == "linux", "Linux consumer must run on Linux")
    def test_linux_consumer_loads_fixture_from_clean_script_path(self):
        consumer_directory = str(compose_ui.ROOT / "tools/consumers")
        clean_path = [path for path in sys.path if path != consumer_directory]
        with patch.object(sys, "path", clean_path.copy()), patch.dict(sys.modules):
            sys.modules.pop("admission_fixture", None)
            with patch.object(compose_ui.stdlib, "javac", side_effect=RuntimeError("native consumer reached")):
                with self.assertRaisesRegex(RuntimeError, "native consumer reached"):
                    compose_ui.probe(self.payload, self.profile, None, None)
            self.assertEqual(sys.path, clean_path)

    def test_linux_application_consumer_rejects_foreign_hosts_before_sdk_access(self):
        for target in ("darwin", "win32"):
            with self.subTest(target=target), patch.object(compose_ui.sys, "platform", target):
                with self.assertRaisesRegex(SystemExit, "requires Linux"):
                    compose_ui.probe(None, None, None, None)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.payload = Path(temporary.name)
        self.files = {"skiko": {compose_ui.GENERATOR: b'File(out).writeText("""\nval version = "${skikoProperties.deployVersion}"\nval skia = "$skiaTag"\n""".trimIndent()',
                                "LICENSE": b"Original fixture grant"}}
        self.profile = {"sources": {"skiko": {}}, "modules": [{"binary": "ui.jar"}],
                        "version": {"skiko": "fixture", "skiaTag": "fixture-skia"},
                        "expectedJars": {compose_ui.jdk.current_target(): {"ui.jar": digest(b"Fixture bytecode")}}}
        self.notices = {"notices/skiko/LICENSE": b"Original fixture grant"}
        (self.payload / "ui.jar").write_bytes(b"Fixture bytecode")
        for name, data in self.notices.items():
            path = self.payload / name
            path.parent.mkdir(parents=True)
            path.write_bytes(data)
        self.write_sources()

    def write_sources(self, files=None, profile=None):
        (self.payload / "compose-ui-sources.jar").write_bytes(jar_bytes(
            compose_ui.support(files or self.files, profile or self.profile)))

    def verify(self):
        compose_ui.verify(self.payload, self.files, self.profile, self.notices)

    def test_registry_binary_extra_runtime_and_changed_notice_fail(self):
        self.verify()
        binary = self.payload / "ui.jar"
        binary.write_bytes(b"Substituted binary")
        with self.assertRaisesRegex(ValueError, "compiler output"):
            self.verify()
        binary.write_bytes(b"Fixture bytecode")
        extra = self.payload / "registry-runtime.jar"
        extra.write_bytes(b"Unreviewed registry library")
        with self.assertRaisesRegex(ValueError, "unexpected files"):
            self.verify()
        extra.unlink()
        notice = self.payload / "notices/skiko/LICENSE"
        notice.write_bytes(b"Replacement grant")
        with self.assertRaisesRegex(ValueError, "notice changed"):
            self.verify()
        notice.unlink()
        with self.assertRaisesRegex(ValueError, "missing or unexpected"):
            self.verify()

    def test_changed_original_generator_or_version_profile_fail(self):
        for change in ("source", "version"):
            files, profile = copy.deepcopy(self.files), copy.deepcopy(self.profile)
            if change == "source":
                files["skiko"][compose_ui.GENERATOR] += b"\n// changed generator"
            else:
                profile["version"]["skiko"] = "unreviewed"
            self.write_sources(files, profile)
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "generation or profile"):
                self.verify()
        broken = copy.deepcopy(self.files)
        broken["skiko"][compose_ui.GENERATOR] = b'File(out).writeText("""$unknown""".trimIndent()'
        with self.assertRaisesRegex(ValueError, "Unresolved"):
            compose_ui.generated_version(broken, self.profile)

    def test_missing_target_pin_fails_even_with_consistent_source_archive(self):
        self.profile["expectedJars"].clear()
        self.write_sources()
        with self.assertRaisesRegex(ValueError, "exact target pins"):
            self.verify()

    def test_display_configuration_preserves_original_and_rejects_changed_input(self):
        original = b"// Original fixture notice\n" + compose_ui.DISPLAY_RATE + b"\n"
        self.files["skiko"][compose_ui.DISPLAY] = original
        self.profile["displayRefreshFallback"] = True
        self.profile["sources"]["skiko"]["files"] = {compose_ui.DISPLAY: digest(original)}
        support = compose_ui.support(self.files, self.profile)
        self.assertEqual(support["originals/skiko/" + compose_ui.DISPLAY], original)
        configured = support["configured/skiko/" + compose_ui.DISPLAY]
        self.assertTrue(configured.startswith(b"// Original fixture notice\n"))
        self.assertIn(b"normalizeDisplayRefreshRate(" + compose_ui.DISPLAY_RATE + b")", configured)
        self.assertIn("CONFIGURATION.txt", support)
        self.write_sources(); self.verify()
        source_archive = self.payload / "compose-ui-sources.jar"
        with zipfile.ZipFile(source_archive) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        entries["configured/skiko/" + compose_ui.DISPLAY] += b"// replacement configuration"
        source_archive.write_bytes(jar_bytes(entries))
        with self.assertRaisesRegex(ValueError, "generation or profile"):
            self.verify()
        self.files["skiko"][compose_ui.DISPLAY] += b"// changed"
        with self.assertRaisesRegex(ValueError, "display refresh source"):
            compose_ui.configured_sources(self.files, self.profile)


if __name__ == "__main__":
    unittest.main()
