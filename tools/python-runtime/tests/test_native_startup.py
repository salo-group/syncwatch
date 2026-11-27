import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import generate_policy
import native_probe
import native_sdk


class NativeStartupTests(unittest.TestCase):
    def setUp(self):
        self.profile = json.loads(generate_policy.PROFILE.read_text(encoding="utf-8"))

    def test_shared_policy_changes_generate_native_values(self):
        original = generate_policy.render(self.profile)
        self.profile["startupPolicy"]["preConfig"]["utf8_mode"] = 0
        self.assertIn("X(utf8_mode, 0)", generate_policy.render(self.profile))
        self.assertNotEqual(original, generate_policy.render(self.profile))

    def test_invalid_native_identifiers_and_types_are_rejected(self):
        for field, value in (("isolated); injected()", 1), ("isolated", True), ("isolated", 0.0), ("isolated", 2)):
            with self.subTest(field=field, value=value):
                profile = copy.deepcopy(self.profile)
                profile["startupPolicy"]["config"] = {field: value}
                with self.assertRaises(ValueError):
                    generate_policy.render(profile)

    def test_duplicate_empty_and_invalid_path_fields_are_rejected(self):
        for fields in ([], ["home", "home"], ["home; injected()"], [None], [{}]):
            with self.subTest(fields=fields):
                self.profile["startupPolicy"]["explicitPathFields"] = fields
                with self.assertRaises(ValueError):
                    generate_policy.render(self.profile)

    def test_stale_generated_policy_blocks_native_startup(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "policy.h"
            path.write_text("stale", encoding="utf-8")
            with patch.object(native_probe, "GENERATED", path), patch.object(native_probe, "resolve") as sdk:
                with self.assertRaisesRegex(ValueError, "shared startup profile"):
                    native_probe.probe()
                sdk.assert_not_called()

    def test_free_threaded_or_debug_sdk_is_rejected(self):
        for variable in ("Py_GIL_DISABLED", "Py_DEBUG"):
            with self.subTest(variable=variable), patch.object(native_sdk.sysconfig, "get_config_var", side_effect=lambda name: name == variable):
                with self.assertRaisesRegex(ValueError, "GIL-enabled"):
                    native_sdk.resolve()

    def test_failed_native_process_propagates(self):
        with self.assertRaisesRegex(ValueError, "command failed"):
            native_probe.execute([sys.executable, "-I", "-S", "-c", "raise SystemExit(7)"], Path.cwd())

    def test_native_diagnostics_are_bounded(self):
        with self.assertRaisesRegex(ValueError, "diagnostic bounds"):
            native_probe.execute([sys.executable, "-I", "-S", "-c", "print('x' * 65537)"], Path.cwd())

    def test_native_failures_redact_private_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            private_file = Path.home() / "sdk" / "header.h"
            args = [sys.executable, "-I", "-S", "-c", "import sys; print(*sys.argv[1:]); raise SystemExit(7)",
                    str(root / "fixture.c"), private_file.as_posix()]
            with self.assertRaisesRegex(ValueError, "exit 7") as failure:
                native_probe.execute(args, root)
            message = str(failure.exception)
            self.assertNotIn(root.as_posix(), message.replace("\\", "/"))
            self.assertNotIn(Path.home().as_posix(), message.replace("\\", "/"))
            self.assertIn("fixture.c", message)
            self.assertIn("header.h", message)

    def test_native_timeout_terminates_child(self):
        with self.assertRaisesRegex(ValueError, "timed out") as failure:
            native_probe.execute([sys.executable, "-I", "-S", "-c", "import time; time.sleep(30)"], Path.cwd(), timeout=0.05)
        self.assertIsNone(failure.exception.__cause__)


if __name__ == "__main__":
    unittest.main()
