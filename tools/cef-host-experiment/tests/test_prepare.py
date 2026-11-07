"""Exercise the preparation interface without executing vendor code."""

import importlib.util
import io
import hashlib
import shutil
import subprocess
from pathlib import Path
import tarfile
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / "prepare.py"
spec = importlib.util.spec_from_file_location("call_host_prepare", MODULE)
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class PreparationTests(unittest.TestCase):
    def test_rejects_wrong_archive_before_creating_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "input.tar.bz2"
            archive.write_bytes(b"not the pinned archive")
            asset = {"sha256": "0" * 64, "compressedBytes": archive.stat().st_size,
                     "expandedBytes": 1, "url": "https://cef-builds.spotifycdn.com/fixture.tar.bz2"}
            with self.assertRaises(ValueError):
                prepare.materialize(asset, archive, root / "output")
            self.assertFalse((root / "output").exists())


    def test_prepares_exact_bytes_and_retains_notices(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "input.tar.bz2"
            with tarfile.open(archive, "w:bz2") as package:
                for name, data in [("fixture/include/cef_app.h", b"public header"),
                                   ("fixture/LICENSE.txt", b"complete vendor notice")]:
                    entry = tarfile.TarInfo(name)
                    entry.size = len(data)
                    entry.mode = 0o644
                    package.addfile(entry, io.BytesIO(data))
            asset = {"sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                     "compressedBytes": archive.stat().st_size, "expandedBytes": 35,
                     "url": "https://cef-builds.spotifycdn.com/fixture.tar.bz2"}
            sdk = prepare.materialize(asset, archive, root / "output")
            self.assertEqual(sdk, root / "output" / "fixture")
            self.assertEqual((sdk / "LICENSE.txt").read_bytes(), b"complete vendor notice")
            self.assertEqual((sdk / "include/cef_app.h").read_bytes(), b"public header")
            with self.assertRaises(FileExistsError):
                prepare.materialize(asset, archive, root / "output")

    def test_archive_cannot_write_outside_its_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, link in [("fixture/../escaped", None), ("fixture/alias", "../../escaped"),
                               ("fixture/alias", "/tmp/escaped")]:
                with self.subTest(name=name, link=link):
                    archive = root / "input.tar.bz2"
                    with tarfile.open(archive, "w:bz2") as package:
                        entry = tarfile.TarInfo(name)
                        if link:
                            entry.type = tarfile.SYMTYPE
                            entry.linkname = link
                            package.addfile(entry)
                        else:
                            entry.size = 1
                            package.addfile(entry, io.BytesIO(b"x"))
                    asset = {"sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                             "compressedBytes": archive.stat().st_size,
                             "expandedBytes": 0 if link else 1,
                             "url": "https://cef-builds.spotifycdn.com/fixture.tar.bz2"}
                    with self.assertRaises(ValueError):
                        prepare.materialize(asset, archive, root / "output")
                    self.assertFalse((root / "output").exists())
                    self.assertFalse((root / "escaped").exists())


    @unittest.skipUnless(shutil.which("cmake"), "CMake is not installed")
    def test_default_configuration_refuses_unqualified_vendor_build(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run([shutil.which("cmake"), "-S", str(MODULE.parent),
                                     "-B", temporary], capture_output=True, text=True, timeout=30)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Unqualified vendor compilation requires an approved research exception",
                          result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
