import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import configuration
from configuration import unix_setup, windows_setup
from source_profile import ROOT, digest, profile_data, read_sources, validate
from runtime_package import inventory, verify


class RuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = profile_data()
        target = "windows-x64" if sys.platform == "win32" else "macos-arm64" if sys.platform == "darwin" else "linux-x64"
        cls.sources = read_sources({n: ROOT / "artifacts/dependencies/cpython-embedded" / target / (file + ".tar")
                                   for n, file in [("cpython", "cpython"), ("zlib", "zlib"), ("unicode", "python-unicode-data")]}, cls.profile)

    def test_original_grants_cannot_be_replaced_by_a_short_label(self):
        sources = copy.deepcopy(self.sources); profile = copy.deepcopy(self.profile)
        from source_profile import digest
        sources["cpython"]["Python/dtoa.c"] = b"SPDX-License-Identifier: LicenseRef-Lucene\n"
        profile["source"]["files"]["Python/dtoa.c"] = digest(sources["cpython"]["Python/dtoa.c"])
        with self.assertRaisesRegex(ValueError, "complete grant"):
            validate(sources, profile)

    def test_complete_notice_paths_match_the_dependency_envelope(self):
        from source_profile import notices
        retained = notices(self.sources, self.profile)
        self.assertEqual(retained["notices/python-unicode-data/LICENSE"], self.sources["unicode"]["LICENSE"])
        self.assertNotIn("notices/unicode/LICENSE", retained)

    def test_unix_configuration_disables_ambient_optional_extensions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / "source"; (source / "Modules").mkdir(parents=True)
            (source / "Modules/Setup.stdlib.in").write_bytes(self.sources["cpython"]["Modules/Setup.stdlib.in"])
            unix_setup(source, root / "build", root / "zlib", root / "libz.a", self.profile)
            text = (root / "build/Modules/Setup.local").read_text()
            self.assertIn("zlib zlibmodule.c -DZ_PREFIX ", text)
            self.assertNotIn("-DZ_PREFIX=", text)
            disabled = text.split("*disabled*\n")[1].splitlines()
            self.assertIn("_ssl", disabled); self.assertIn("_ctypes", disabled)
            self.assertNotIn("array", disabled)

    def test_windows_selects_modules_with_the_original_generator_and_project_files(self):
        import xml.etree.ElementTree as ET
        with tempfile.TemporaryDirectory() as tmp:
            source, build = Path(tmp) / "source", Path(tmp) / "build"
            original_files = ["Modules/Setup.bootstrap.in", "Modules/config.c.in", "PC/config.c",
                              "PCbuild/pythoncore.vcxproj", "PCbuild/pcbuild.proj", "Tools/freeze/makeconfig.py"]
            for name in original_files:
                path = source / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(self.sources["cpython"][name])
            modules, expected = windows_setup(source, build, self.profile)
            table = build / "config.c"
            subprocess.run([sys.executable, "-I", "-B", str(source / "Tools/freeze/makeconfig.py"),
                            str(source / "Modules/config.c.in"), str(table), *modules],
                           check=True, timeout=10)
            actual = configuration.builtin_modules(table)
            self.assertEqual(sorted(actual), sorted(expected))
            self.assertTrue({"_imp", "_string", "winreg", "_ast", "_tokenize", "zlib"} <= set(actual))
            self.assertNotIn("_random", actual)
            ns = {"m": "http://schemas.microsoft.com/developer/msbuild/2003"}
            excluded = {item.get("Remove") for item in ET.parse(build / "module-selection.props").findall(".//m:ClCompile", ns)}
            self.assertIn("..\\Modules\\_randommodule.c", excluded)
            self.assertNotIn("..\\Modules\\zlibmodule.c", excluded)
            for name in original_files:
                self.assertEqual((source / name).read_bytes(), self.sources["cpython"][name])

    @unittest.skipIf(sys.platform == "win32", "Unix Make build")
    def test_make_configures_native_link_inputs_without_rewriting_the_makefile(self):
        with tempfile.TemporaryDirectory() as tmp:
            build = Path(tmp)
            makefile = build / "Makefile"
            original = ("VPATH = /source/absolute\nSHLIBS = -lsystem\nMODLIBS = -lowned\n"
                        "LDVERSION = 3.12\nreport:\n\t@printf '%s\\n' '$(VPATH)' '$(SHLIBS)'\n")
            makefile.write_text(original, encoding="utf-8")
            (build / configuration.UNIX_MAKEFILE.name).write_bytes(configuration.UNIX_MAKEFILE.read_bytes())
            for platform, libraries in [
                ("darwin", "-lsystem -lowned -Wl,-install_name,@rpath/libpython3.12.dylib"),
                ("linux", "-lsystem"),
            ]:
                with self.subTest(platform=platform):
                    result = subprocess.check_output(configuration.unix_make("MACHDEP=" + platform, "report"),
                                                     cwd=build, text=True, timeout=10)
                    self.assertEqual(result.splitlines(), ["../cpython", libraries])
                    self.assertEqual(makefile.read_text(encoding="utf-8"), original)
            setup = build / "Modules/Setup.local"
            setup.parent.mkdir()
            setup.write_text("zlib zlibmodule.c -I/source/absolute\n", encoding="utf-8")
            packaged = configuration.build_configuration(build, "macos-arm64")
            self.assertEqual(set(packaged), {"unix-runtime.mk", "generated-build-digests.json"})
            self.assertEqual(json.loads(packaged["generated-build-digests.json"])["Makefile"],
                             digest(makefile.read_bytes()))

    def test_missing_extra_and_corrupt_payload_fail_before_native_execution(self):
        sdk = {"target": "linux-x64"}
        from source_profile import digest, encoded
        for operation in ["missing", "extra", "corrupt"]:
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); original = root / "library"; original.write_bytes(b"original")
                report = {"schemaVersion": 1, "sdk": sdk, "profileSha256": digest(encoded(self.profile)),
                          "pinnedRuntimeBuilt": True, "pinnedEmbeddingAbiVerified": True,
                          "syncplayRuntimeReady": False, "files": inventory(root)}
                (root / "build-report.json").write_bytes(encoded(report))
                if operation == "missing": original.unlink()
                elif operation == "extra": (root / "extra").write_bytes(b"extra")
                else: original.write_bytes(b"corrupt!")
                with patch("runtime_package.relocated_probe") as native:
                    with self.assertRaisesRegex(ValueError, "missing, extra or changed"):
                        verify(root, self.sources, self.profile, sdk, [])
                    native.assert_not_called()


if __name__ == "__main__":
    unittest.main()
