"""Exercise the actual macOS symbol audit without building CPython."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from binary_audit import require_bound_macos_symbols, require_no_build_paths


@unittest.skipUnless(sys.platform == "darwin", "macOS dynamic library audit")
class BinaryAuditTests(unittest.TestCase):
    def test_rejects_dynamic_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            build = Path(tmp)
            source = build / "fixture.c"
            source.write_text("extern int missing(void);\nint fixture(void) { return missing(); }\n",
                              encoding="utf-8")
            library = build / "libfixture.dylib"
            subprocess.run(["xcrun", "clang", "-dynamiclib", "-undefined", "dynamic_lookup",
                            str(source), "-o", str(library)], check=True, timeout=30)
            with self.assertRaisesRegex(ValueError, "without a declared macOS library"):
                require_bound_macos_symbols(library)


class BuildPathTests(unittest.TestCase):
    def test_package_gate_rejects_native_build_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary).resolve()
            library = work / "runtime-fixture"
            library.write_bytes(b"syncwatch-build/runtime-fixture")
            require_no_build_paths([library], work)
            for encoding in ("utf-8", "utf-16-le"):
                with self.subTest(encoding=encoding):
                    library.write_bytes(str(work).encode(encoding))
                    with self.assertRaisesRegex(ValueError, "private build directory in runtime-fixture") as error:
                        require_no_build_paths([library], work)
                    self.assertNotIn(str(work), str(error.exception))
