import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "dependency_plan.py"


class DependencyPlanChecks(unittest.TestCase):
    def run_partition(self, matrix):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "outputs"
            environment = dict(os.environ, CI_DEPENDENCY_MATRIX=json.dumps(matrix), GITHUB_OUTPUT=str(output))
            result = subprocess.run([sys.executable, str(SCRIPT)], env=environment, capture_output=True, text=True)
            values = dict(line.split("=", 1) for line in output.read_text().splitlines()) if output.exists() else {}
            return result, {key: json.loads(value) for key, value in values.items()}

    def test_partition_preserves_every_selected_build_and_its_cold_flag(self):
        linux = {"component": "fastjsonschema", "platform": "linux-x64", "cold": False}
        macos = {"component": "fastjsonschema", "platform": "macos-arm64", "cold": False}
        windows = {"component": "fastjsonschema", "platform": "windows-x64", "cold": True}
        kotlin = {"component": "kotlin-stdlib", "platform": "linux-x64", "cold": True}
        result, outputs = self.run_partition({"include": [linux, macos, windows, kotlin]})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(outputs, {"linux": {"include": [linux, kotlin]}, "macos": {"include": [macos]},
                                   "windows": {"include": [windows]}})

    def test_native_lanes_wait_only_for_their_own_verified_platform(self):
        root = Path(__file__).resolve().parents[3]
        jobs = json.loads((root / ".github/workflows/repository.yml").read_text())["jobs"]
        for platform in ("linux", "macos", "windows"):
            with self.subTest(platform=platform):
                self.assertEqual(jobs[platform]["needs"], [f"dependencies-{platform}", "dependency-plan"])

    def test_invalid_partition_cannot_publish_partial_platform_outputs(self):
        entries = [{"component": "fastjsonschema", "platform": platform, "cold": False}
                   for platform in ("linux-x64", "macos-arm64", "windows-x64")]
        for invalid in (None, {}, {"include": []}, {"include": entries[:2]},
                        {"include": entries + [entries[0]]},
                        {"include": entries + [{"component": "other", "platform": "unknown", "cold": False}]},
                        {"include": entries + [{"component": "other", "platform": "linux-x64", "cold": "false"}]}):
            with self.subTest(matrix=invalid):
                result, outputs = self.run_partition(invalid)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(outputs, {})

    def test_windows_regressions_start_independently_and_remain_required(self):
        root = Path(__file__).resolve().parents[3]
        jobs = json.loads((root / ".github/workflows/repository.yml").read_text())["jobs"]
        regressions = jobs["windows-regressions"]
        self.assertEqual(regressions["needs"], ["dependency-plan"])
        self.assertEqual(regressions["if"], "${{ needs.dependency-plan.outputs.mode == 'full' }}")
        self.assertEqual(regressions["runs-on"], "windows-2025")
        self.assertIn("windows-regressions", jobs["required"]["needs"])

    def test_metadata_result_requires_both_windows_qualifications(self):
        root = Path(__file__).resolve().parents[3]
        jobs = json.loads((root / ".github/workflows/repository.yml").read_text())["jobs"]
        self.assertEqual(jobs["metadata"]["needs"], ["windows", "windows-regressions", "dependency-plan"])


if __name__ == "__main__":
    unittest.main()
