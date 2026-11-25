from pathlib import Path
import platform
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from candidate_tree import assemble
import macos


@unittest.skipUnless(platform.system() == "Darwin" and platform.machine() == "arm64",
                     "The macOS package is sealed and checked on its native host")
class MacOSBundleTests(unittest.TestCase):
    def test_seals_the_complete_bundle_with_unchanged_runtime_files(self):
        with tempfile.TemporaryDirectory(prefix="macos package ") as temporary:
            root = Path(temporary)
            source = root / "entry.c"
            source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
            binary = root / "SyncWatch"
            subprocess.run(["cc", "-arch", "arm64", str(source), "-o", str(binary)], check=True, timeout=20)
            media = root / "media"
            helper = root / "helper"
            for path in (media, helper):
                path.mkdir()
                (path / "license.txt").write_bytes(b"original fixture contents")
            components = macos.prepare(SimpleNamespace(application=binary, media_package=media, helper_package=helper),
                                       root / "work")
            output = root / "candidate"
            assemble(output, components, platform="macos-arm64", source_commit="a" * 40)
            bundle = output / "contents/SyncWatch.app"
            subprocess.run(["codesign", "--verify", "--deep", "--strict", str(bundle)], check=True, timeout=20)
            for name, original in (("Media", media), ("Syncplay", helper)):
                self.assertEqual((bundle / "Contents/Resources" / name / "license.txt").read_bytes(),
                                 (original / "license.txt").read_bytes())
            self.assertEqual((bundle / "Contents/Resources/LICENSE").read_bytes(),
                             (macos.ROOT / "LICENSE").read_bytes())
