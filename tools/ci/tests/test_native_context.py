import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("native_context_test", ROOT / "tools/native/native_context.py")
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


class NativeContextTests(unittest.TestCase):
    def test_existing_sdk_consumer_does_not_require_autotools(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(native, "native_target", return_value="linux-x64"):
                with patch.object(native.shutil, "which", return_value=None):
                    context = native.BuildContext(Path(temporary) / "consumer", 1, autotools=False)
                    with self.assertRaisesRegex(ValueError, "Missing native developer tool: gcc"):
                        context.tool(context.env["CC"])
                    with self.assertRaisesRegex(ValueError, "Missing native developer tool: m4"):
                        native.BuildContext(Path(temporary) / "producer", 1)


if __name__ == "__main__":
    unittest.main()
