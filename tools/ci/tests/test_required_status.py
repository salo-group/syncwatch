import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from required_status import validate_results

WORKFLOW_JOBS = ["repository", "tests", "contracts", "macos", "windows", "linux", "metadata", "dependency-plan",
                 "dependencies-linux", "dependencies-macos", "dependencies-windows", "windows-regressions"]


class RequiredStatusChecks(unittest.TestCase):
    def run_gate(self, results, jobs=WORKFLOW_JOBS):
        script = Path(__file__).resolve().parents[1] / "required_status.py"
        environment = dict(os.environ, CI_NEEDS_JSON=json.dumps(results))
        return subprocess.run([sys.executable, str(script), *jobs], env=environment, capture_output=True, text=True)

    def workflow_results(self, mode):
        results = {name: {"result": "success"} for name in WORKFLOW_JOBS}
        if mode == "documentation":
            for name in ("macos", "windows", "linux", "metadata", "windows-regressions"):
                results[name]["result"] = "skipped"
        results["dependency-plan"]["outputs"] = {"mode": mode}
        return results

    def test_all_selected_jobs_must_succeed(self):
        validate_results({"repository": {"result": "success"}, "tests": {"result": "success"}}, ["repository", "tests"])

    def test_non_success_results_fail_closed(self):
        for result in ("failure", "cancelled", "skipped", "pending", "neutral", "unknown", None):
            with self.subTest(result=result), self.assertRaises(ValueError):
                validate_results({"tests": {"result": result}}, ["tests"])

    def test_missing_result_fails(self):
        with self.assertRaises(ValueError):
            validate_results({"tests": {}}, ["tests"])

    def test_missing_job_fails(self):
        with self.assertRaises(ValueError):
            validate_results({}, ["tests"])

    def test_unexpected_job_fails(self):
        with self.assertRaises(ValueError):
            validate_results({"tests": {"result": "success"}, "extra": {"result": "failure"}}, ["tests"])

    def test_empty_job_set_cannot_pass(self):
        with self.assertRaises(ValueError):
            validate_results({}, [])

    def test_duplicate_expected_jobs_fail(self):
        with self.assertRaises(ValueError):
            validate_results({"tests": {"result": "success"}}, ["tests", "tests"])

    def test_cli_propagates_failure(self):
        script = Path(__file__).resolve().parents[1] / "required_status.py"
        environment = dict(os.environ, CI_NEEDS_JSON=json.dumps({"tests": {"result": "failure"}}))
        result = subprocess.run([sys.executable, str(script), "tests"], env=environment, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("did not succeed", result.stderr)

    def test_documentation_cli_accepts_only_explicitly_unselected_native_jobs(self):
        results = {name: {"result": "success"} for name in WORKFLOW_JOBS}
        for name in ("macos", "windows", "linux", "metadata", "windows-regressions"):
            results[name]["result"] = "skipped"
        results["dependency-plan"]["outputs"] = {"mode": "documentation"}
        environment = dict(os.environ, CI_NEEDS_JSON=json.dumps(results))
        script = Path(__file__).resolve().parents[1] / "required_status.py"
        result = subprocess.run([sys.executable, str(script), *WORKFLOW_JOBS], env=environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_full_cli_accepts_all_successful_jobs_with_a_valid_plan(self):
        result = self.run_gate(self.workflow_results("full"))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_documentation_cannot_mask_failed_or_cancelled_jobs(self):
        for name in WORKFLOW_JOBS:
            for status in ("failure", "cancelled"):
                with self.subTest(name=name, status=status):
                    results = self.workflow_results("documentation")
                    results[name]["result"] = status
                    result = self.run_gate(results)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("Required CI failed:", result.stderr)

    def test_documentation_cannot_skip_selected_jobs_or_omit_native_results(self):
        for name in ("repository", "tests", "contracts", "dependency-plan", "dependencies-linux", "dependencies-macos", "dependencies-windows"):
            with self.subTest(name=name):
                results = self.workflow_results("documentation")
                results[name]["result"] = "skipped"
                self.assertNotEqual(self.run_gate(results).returncode, 0)
        for mode in ("full", "documentation"):
            for name in WORKFLOW_JOBS:
                with self.subTest(mode=mode, missing=name):
                    results = self.workflow_results(mode)
                    results.pop(name)
                    self.assertNotEqual(self.run_gate(results).returncode, 0)
        results = self.workflow_results("documentation")
        results.pop("linux")
        self.assertNotEqual(self.run_gate(results, [name for name in WORKFLOW_JOBS if name != "linux"]).returncode, 0)

    def test_full_validation_cannot_skip_any_job(self):
        for name in WORKFLOW_JOBS:
            with self.subTest(name=name):
                results = self.workflow_results("full")
                results[name]["result"] = "skipped"
                self.assertNotEqual(self.run_gate(results).returncode, 0)

    def test_unknown_or_missing_plan_modes_fail_with_a_bounded_diagnostic(self):
        for mode in (None, "", "unknown", False, 42, [], {}):
            with self.subTest(mode=mode):
                result = self.run_gate(self.workflow_results(mode))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Required CI failed: dependency plan has no valid validation mode", result.stderr)
        results = self.workflow_results("documentation")
        results["dependency-plan"].pop("outputs")
        self.assertNotEqual(self.run_gate(results).returncode, 0)


if __name__ == "__main__":
    unittest.main()
