"""Exercise the helper producer's complete source-copy/receipt path without a compiler SDK."""

import importlib.util
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

BRIDGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE))
import helper_sources

spec = importlib.util.spec_from_file_location("helper_producer", BRIDGE / "build.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class HelperBuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="sw-helper-producer-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.git("init", "-q")
        validator = self.root / "3rdparty/fastjsonschema"
        validator.mkdir(parents=True)
        self.git("init", "-q", cwd=validator)
        for name, data in {"LICENSE": "Authored fixture grant\n", "fastjsonschema/api.py": "fixture = 1\n"}.items():
            path = validator / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data.encode())
        self.git("add", ".", cwd=validator)
        self.commit(validator)
        self.validator_pin = self.git("rev-parse", "HEAD", cwd=validator)
        for name in ("packages/syncplay-helper/syncwatch_bridge/__init__.py", "packages/ipc/syncwatch_ipc/__init__.py",
                     "packages/python-startup/policy.json", "contracts/fixture.json", "tools/bridge/fixture.py"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}\n")
        self.git("add", "packages", "contracts", "tools")
        for name, pin in (("syncplay", "a" * 40), ("fastjsonschema", self.validator_pin)):
            self.git("update-index", "--add", "--cacheinfo", "160000," + pin + ",3rdparty/" + name)
        self.commit(self.root)
        # The producer must export the pinned Git objects, even if a checkout
        # file differs. This is not imported or executed as validator code.
        (validator / "fastjsonschema/api.py").write_text("changed working tree\n")
        self.target = {"Darwin": "macos-arm64", "Linux": "linux-x64", "Windows": "windows-x64"}[platform.system()]
        self.runtime = self.root / "artifacts/engine"
        self.runtime.mkdir(parents=True)
        (self.runtime / "engine.bin").write_bytes(b"authored runtime fixture")
        (self.runtime / "build-report.json").write_text(json.dumps({
            "files": builder.inventory(self.runtime), "sourceBuiltHeadlessEngine": True,
            "sdk": {"native": {"target": self.target}},
        }))
        self.output = self.root / "artifacts/helper"

    def git(self, *arguments, cwd=None):
        return subprocess.check_output(["git", *arguments], cwd=cwd or self.root, text=True, stderr=subprocess.DEVNULL).strip()

    def commit(self, directory):
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "Fixture", cwd=directory)

    def produce(self, mutate=False):
        original_run = subprocess.run
        def run(command, **options):
            if command[0] != "fixture-cmake":
                return original_run(command, **options)
            if "--build" in command:
                directory = Path(command[command.index("--build") + 1])
                path = directory / ("Release/syncwatch-helper.exe" if platform.system() == "Windows" else "syncwatch-helper")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"authored helper fixture")
                if mutate:
                    (self.root / "contracts/fixture.json").write_text("changed during build\n")
            return subprocess.CompletedProcess(command, 0)
        with patch.object(builder, "ROOT", self.root), patch.object(builder, "resolve", return_value=("fixture-cmake", [], {"target": self.target})), \
                patch.object(builder, "first_party_inputs", side_effect=lambda: helper_sources.first_party_inputs(self.root)), \
                patch.object(builder.subprocess, "run", side_effect=run):
            return builder.build(self.runtime, self.output)

    def test_complete_producer_hashes_pinned_validator_exports_and_publishes_matching_receipt(self):
        binary = self.produce()
        self.assertEqual(binary.read_bytes(), b"authored helper fixture")
        self.assertEqual((self.output / "app/fastjsonschema/api.py").read_bytes(), b"fixture = 1\n")
        receipt = json.loads((self.output / "helper-report.json").read_text())
        self.assertEqual(receipt["validatorRevision"], self.validator_pin)
        for name in ("app/fastjsonschema/api.py", "licenses/fastjsonschema/LICENSE"):
            self.assertEqual(receipt["validatorOriginals"][name], builder.digest(self.output / name))
        actual = builder.inventory(self.output)
        actual.pop("helper-report.json")
        self.assertEqual(actual, receipt["files"])
        self.assertFalse(list(self.output.parent.glob("sw-helper-build-*")))

    def test_changed_first_party_inputs_do_not_publish_a_partial_helper(self):
        with self.assertRaisesRegex(ValueError, "inputs changed"):
            self.produce(mutate=True)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.output.parent.glob("sw-helper-build-*")))

    def test_old_stock_player_runtime_requires_rebuild(self):
        report = self.runtime / "build-report.json"
        receipt = json.loads(report.read_text())
        library = "bin" if self.target == "windows-x64" else "lib"
        entries = {
            "module": library + "/syncplay/players/basePlayer.py",
            "source": "configuration/syncplay/syncplay/players/__init__.py",
        }
        for kind, name in entries.items():
            with self.subTest(name=name):
                self.output = self.output.with_name("helper-" + kind)
                entry = self.runtime / name
                entry.parent.mkdir(parents=True, exist_ok=True)
                entry.write_bytes(b"authored legacy runtime fixture")
                report.unlink()
                receipt["files"] = builder.inventory(self.runtime)
                report.write_text(json.dumps(receipt))
                try:
                    with self.assertRaisesRegex(ValueError, "Rebuild the headless Syncplay runtime"):
                        self.produce()
                    self.assertFalse(self.output.exists())
                finally:
                    entry.unlink()
