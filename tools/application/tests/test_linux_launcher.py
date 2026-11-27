import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(sys.platform == "linux", "Linux executable launcher is checked on Linux only")
class LinuxLauncherTests(unittest.TestCase):
    def test_relocation_uses_own_runtime_argv_and_environment_from_other_working_directory(self):
        with tempfile.TemporaryDirectory(prefix="sw-launcher-") as temporary:
            root = Path(temporary)
            application = root / "build"
            (application / "Runtime/bin").mkdir(parents=True)
            launcher = application / "SyncWatch"
            subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                            str(Path(__file__).resolve().parents[1] / "launcher_linux.c"), "-o", str(launcher)],
                           check=True, timeout=30)
            java = application / "Runtime/bin/java"
            java.write_text("#!" + sys.executable + "\nimport os,sys,json\nprint(json.dumps({'argv':sys.argv[1:],'cwd':os.getcwd(),'env':dict(os.environ)}))\n")
            java.chmod(0o755)
            (application / "Renderer/share/X11").mkdir(parents=True)
            (application / "Renderer/share/X11/XErrorDB").write_text("owned error data")
            relocated = root / "Кино 映画 with spaces"
            application.rename(relocated)
            poisoned = {name: "injected" for name in ("CLASSPATH", "JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS",
                                                      "FONTCONFIG_FILE", "ALSA_CONFIG_PATH", "LD_LIBRARY_PATH", "XERRORDB", "XKEYSYMDB")}
            result = subprocess.run([str(relocated / "SyncWatch"), "--state", "state with spaces"], cwd=root,
                                    env=os.environ | poisoned, capture_output=True, text=True, check=True, timeout=10)
            actual = json.loads(result.stdout)
            self.assertEqual(actual["argv"], ["-Dskiko.vsync.enabled=false", "@launch.args",
                                              "--state", "state with spaces"])
            self.assertEqual(actual["cwd"], str(relocated))
            for name in ("CLASSPATH", "JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS"):
                self.assertNotIn(name, actual["env"])
            self.assertEqual(actual["env"]["FONTCONFIG_FILE"], str(relocated / "Renderer/share/fonts/fonts.conf"))
            self.assertEqual(actual["env"]["ALSA_CONFIG_PATH"], str(relocated / "Runtime/share/alsa/alsa.conf"))
            self.assertEqual(actual["env"]["XERRORDB"], str(relocated / "Renderer/share/X11/XErrorDB"))
            self.assertNotIn("XKEYSYMDB", actual["env"])
            self.assertEqual(actual["env"]["LD_LIBRARY_PATH"].split(":"),
                             [str(relocated / path) for path in ("Renderer/runtime", "Media/lib", "Runtime/lib", "Runtime/lib/server", "Syncplay/lib", "Clients/lib")])
            java = relocated / "Runtime/bin/java"
            java.unlink()
            failed = subprocess.run([str(relocated / "SyncWatch")], cwd=root, capture_output=True, timeout=10)
            self.assertEqual(failed.returncode, 127)
            self.assertIn(b"SyncWatch:", failed.stderr)
