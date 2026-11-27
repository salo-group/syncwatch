from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from native_libraries import openssl


class OpenSSLRecipeTests(unittest.TestCase):
    def test_configured_paths_are_independent_of_the_staging_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            for target, installation in [("macos-arm64", "/syncwatch/openssl"),
                                         ("linux-x64", "/syncwatch/openssl"),
                                         ("windows-x64", "C:/SyncWatch/OpenSSL")]:
                with self.subTest(target=target), patch("native_libraries.execute") as execute:
                    work = Path(temporary) / target
                    source = work / "original-source"
                    source.mkdir(parents=True)
                    prefix, _ = openssl(source, work, {"target": target}, "perl", {"SYNCWATCH_NMAKE": "nmake"})
                    by_step = {call.args[0]: call.args[1] for call in execute.call_args_list}
                    configure = by_step["configure"]
                    self.assertEqual(configure[1], "../original-source/Configure")
                    self.assertIn("--prefix=" + installation, configure)
                    self.assertIn("no-autoload-config", configure)
                    self.assertNotIn(str(work), " ".join(configure))
                    self.assertIn("DESTDIR=" + (work / "openssl-stage").as_posix(), by_step["install"])
                    self.assertTrue(prefix.is_relative_to(work / "openssl-stage"))
                    compiler_env = execute.call_args_list[1].args[4]
                    if target == "windows-x64":
                        self.assertIn(f"/pathmap:{work}=syncwatch-build", compiler_env["_CL_"])
                        self.assertIn("/PDBALTPATH:%_PDB%", compiler_env["_LINK_"])
                    else:
                        self.assertNotIn("_CL_", compiler_env)
                        self.assertNotIn("_LINK_", compiler_env)
