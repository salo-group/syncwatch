import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_repository import (
    ValidationError,
    check_design,
    check_document,
    check_repository,
    check_server_manifests,
    check_native_manifests,
    check_submodules,
    check_text,
    check_workflows,
    load_json,
)


REPOSITORY = Path(__file__).resolve().parents[3]


class RepositoryChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        return path

    def test_current_repository_passes(self):
        check_repository(REPOSITORY)

    def test_trailing_whitespace_fails(self):
        with self.assertRaisesRegex(ValidationError, "trailing whitespace"):
            check_text(self.write("bad.py", "value = 1 \n"))

    def test_missing_final_newline_fails(self):
        with self.assertRaisesRegex(ValidationError, "final newline"):
            check_text(self.write("bad.md", "# Missing newline"))

    def test_duplicate_json_key_fails(self):
        with self.assertRaisesRegex(ValidationError, "duplicate JSON key"):
            load_json(self.write("bad.json", '{"value": 1, "value": 2}\n'))

    def test_nonfinite_json_fails(self):
        with self.assertRaisesRegex(ValidationError, "non-finite"):
            load_json(self.write("bad.json", '{"value": NaN}\n'))

    def test_broken_documentation_link_fails(self):
        with self.assertRaisesRegex(ValidationError, "broken local link"):
            check_document(self.write("README.md", "[Missing](missing.md)\n"), self.root)

    def test_reference_style_broken_link_fails(self):
        with self.assertRaisesRegex(ValidationError, "broken local link"):
            check_document(self.write("README.md", "[Guide][guide]\n\n[guide]: missing.md\n"), self.root)

    def test_code_examples_are_not_resolved_as_links(self):
        document = self.write("README.md", "```md\n[Example](absent.md)\n```\n")
        check_document(document, self.root)

    def test_documentation_link_cannot_escape_repository(self):
        with self.assertRaisesRegex(ValidationError, "leaves the repository"):
            check_document(self.write("README.md", "[Outside](../outside.md)\n"), self.root)

    def test_documentation_language_fails(self):
        with self.assertRaisesRegex(ValidationError, "English"):
            check_document(self.write("README.md", "\u041f\u0440\u0438\u0432\u0435\u0442\n"), self.root)

    def test_external_workspace_reference_fails(self):
        with self.assertRaisesRegex(ValidationError, "self-contained"):
            check_document(self.write("README.md", "Use /home/example/project.\n"), self.root)

    def test_local_and_external_links_pass(self):
        self.write("docs/guide.md", "# Guide\n")
        check_document(
            self.write("README.md", "[Guide](docs/guide.md) and [Web](https://example.com).\n"),
            self.root,
        )

    def copy_design(self):
        for member in ("macos", "windows", "linux"):
            (self.root / "packages/design" / member).mkdir(parents=True, exist_ok=True)
        for source in (REPOSITORY / "packages/design").rglob("*.json"):
            self.write(str(source.relative_to(REPOSITORY)), source.read_text(encoding="utf-8"))

        for member in ("LICENSE", "README.md"):
            self.write("packages/design/" + member, (REPOSITORY / "packages/design" / member).read_text(encoding="utf-8"))

    def test_missing_token_reference_fails(self):
        self.copy_design()
        path = self.root / "packages/design/components.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["components"]["LibraryLayout"]["tokens"].append("unknown.token")
        path.write_text(json.dumps(data) + "\n")
        with self.assertRaisesRegex(ValidationError, "unknown token"):
            check_design(self.root)

    def test_invalid_geometry_fails(self):
        self.copy_design()
        path = self.root / "packages/design/tokens/geometry.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["layout"]["workspacePadding"]["value"] = True
        path.write_text(json.dumps(data) + "\n")
        with self.assertRaisesRegex(ValidationError, "numeric token"):
            check_design(self.root)

    def initialize_submodule_fixture(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.write(
            ".gitmodules",
            '[submodule "source"]\n\tpath = 3rdparty/source\n'
            '\turl = https://github.com/example/source.git\n',
        )
        subprocess.run(
            ["git", "update-index", "--add", "--cacheinfo", "160000", "a" * 40, "3rdparty/source"],
            cwd=self.root,
            check=True,
        )

    def test_pinned_gitlink_passes_without_source_download(self):
        self.initialize_submodule_fixture()
        check_submodules(self.root)

    def test_missing_gitlink_fails(self):
        self.initialize_submodule_fixture()
        subprocess.run(["git", "update-index", "--force-remove", "3rdparty/source"], cwd=self.root, check=True)
        with self.assertRaisesRegex(ValidationError, "Git links differ"):
            check_submodules(self.root)

    def test_floating_submodule_branch_fails(self):
        self.initialize_submodule_fixture()
        path = self.root / ".gitmodules"
        path.write_text(path.read_text(encoding="utf-8") + "\tbranch = main\n")
        with self.assertRaisesRegex(ValidationError, "floating branches"):
            check_submodules(self.root)

    def test_copied_vendor_source_fails(self):
        self.initialize_submodule_fixture()
        self.write("3rdparty/copied/source.py", "value = 1\n")
        subprocess.run(["git", "add", "3rdparty/copied/source.py"], cwd=self.root, check=True)
        with self.assertRaisesRegex(ValidationError, "must be a Git submodule"):
            check_submodules(self.root)

    def test_design_aliases_resolve_and_reject_invalid_chains(self):
        self.copy_design()
        path = self.root / "packages/design/tokens/geometry.json"
        source = json.loads(path.read_text(encoding="utf-8"))
        source["layout"]["alias"] = {"type": "number", "value": "{layout.panelGap}"}
        path.write_text(json.dumps(source))
        check_design(self.root)
        for reference, error in (("{missing}", "unknown design reference"),
                                 ("{color.shared.accent}", "type mismatch"),
                                 ("{layout.alias}", "cyclic design reference")):
            source["layout"]["alias"]["value"] = reference
            path.write_text(json.dumps(source))
            with self.subTest(reference=reference), self.assertRaisesRegex(ValidationError, error):
                check_design(self.root)

    def test_design_theme_cannot_reference_dimensions(self):
        self.copy_design()
        path = self.root / "packages/design/themes.json"
        source = json.loads(path.read_text(encoding="utf-8"))
        source["themes"]["dark"]["text"] = "layout.panelGap"
        path.write_text(json.dumps(source))
        with self.assertRaisesRegex(ValidationError, "invalid design theme color"):
            check_design(self.root)

    def copy_native_manifests(self):
        files = ["global.json", "tools/native/toolchains.json", "NuGet.Config", "SyncWatch.slnx"]
        solution = ET.parse(REPOSITORY / "SyncWatch.slnx").getroot()
        files += [project.attrib["Path"] for project in solution.findall(".//Project")]
        for name in files:
            self.write(name, (REPOSITORY / name).read_text(encoding="utf-8"))

    def test_native_sdk_roll_forward_fails(self):
        self.copy_native_manifests()
        path = self.root / "global.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["sdk"]["rollForward"] = "latestMajor"
        path.write_text(json.dumps(data) + "\n")
        with self.assertRaisesRegex(ValidationError, "exact toolchain pin"):
            check_native_manifests(self.root)

    def test_native_registry_sources_fail(self):
        self.copy_native_manifests()
        self.write("NuGet.Config", '<configuration><packageSources><add key="registry" value="https://example.invalid" /></packageSources></configuration>\n')
        with self.assertRaisesRegex(ValidationError, "no registry sources"):
            check_native_manifests(self.root)

    def test_native_package_reference_fails(self):
        self.copy_native_manifests()
        path = self.root / "packages/windows-domain/SyncWatch.Domain/SyncWatch.Domain.csproj"
        content = ET.parse(path)
        ET.SubElement(content.getroot(), "PackageReference", {"Include": "Unpinned.Library", "Version": "1.0"})
        content.write(path)
        with self.assertRaisesRegex(ValidationError, "pinned local sources"):
            check_native_manifests(self.root)

    def test_application_tests_require_windows_framework(self):
        self.copy_native_manifests()
        check_native_manifests(self.root)
        path = self.root / "apps/windows/SyncWatch.Tests/SyncWatch.Tests.csproj"
        content = ET.parse(path)
        content.find(".//TargetFramework").text = "net10.0"
        content.write(path)
        with self.assertRaisesRegex(ValidationError, "native target framework"):
            check_native_manifests(self.root)

    def test_native_project_reference_cannot_escape_repository(self):
        self.copy_native_manifests()
        path = self.root / "apps/windows/SyncWatch/SyncWatch.csproj"
        content = ET.parse(path)
        content.find(".//ProjectReference").set("Include", "../../../../outside.csproj")
        content.write(path)
        with self.assertRaisesRegex(ValidationError, "stay local"):
            check_native_manifests(self.root)


class ServerManifestChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name in ["global.json", "tools/server/toolchains.json", "services/room-metadata/RoomMetadata.slnx",
                     "services/room-metadata/RoomMetadata/RoomMetadata.csproj", "services/room-metadata/RoomMetadata.Tests/RoomMetadata.Tests.csproj"]:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text((REPOSITORY / name).read_text(encoding="utf-8"))

    def test_server_cannot_add_undeclared_registry_libraries(self):
        path = self.root / "services/room-metadata/RoomMetadata/RoomMetadata.csproj"
        content = ET.parse(path)
        ET.SubElement(content.getroot(), "PackageReference", {"Include": "Unpinned.Library", "Version": "1.0"})
        content.write(path)
        with self.assertRaisesRegex(ValidationError, "pinned local sources"):
            check_server_manifests(self.root)

    def test_server_sdk_archive_must_use_declared_official_source(self):
        path = self.root / "tools/server/toolchains.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["sources"]["linux-x64"]["url"] = "https://example.invalid/sdk.tar.gz"
        path.write_text(json.dumps(data) + "\n")
        with self.assertRaisesRegex(ValidationError, "official URLs"):
            check_server_manifests(self.root)

    def test_server_project_reference_must_stay_inside_checkout(self):
        path = self.root / "services/room-metadata/RoomMetadata.Tests/RoomMetadata.Tests.csproj"
        content = ET.parse(path)
        content.find(".//ProjectReference").set("Include", "../../../../outside.csproj")
        content.write(path)
        with self.assertRaisesRegex(ValidationError, "stay local"):
            check_server_manifests(self.root)


class WorkflowChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / ".github/workflows/repository.yml"
        self.path.parent.mkdir(parents=True)
        self.workflow = json.loads((REPOSITORY / ".github/workflows/repository.yml").read_text(encoding="utf-8"))
        action = self.root / ".github/actions/upload-native/action.yml"
        action.parent.mkdir(parents=True)
        action.write_text((REPOSITORY / ".github/actions/upload-native/action.yml").read_text(encoding="utf-8"))
        reusable = self.root / ".github/workflows/dependency-build.yml"
        reusable.write_text((REPOSITORY / ".github/workflows/dependency-build.yml").read_text(encoding="utf-8"))
        action = self.root / ".github/actions/dependency-build/action.yml"
        action.parent.mkdir(parents=True)
        action.write_text((REPOSITORY / ".github/actions/dependency-build/action.yml").read_text(encoding="utf-8"))

    def validate(self, mutate):
        workflow = copy.deepcopy(self.workflow)
        mutate(workflow)
        self.path.write_text(json.dumps(workflow) + "\n")
        check_workflows(self.root)

    def test_jvm_metadata_tests_cannot_be_omitted(self):
        with self.assertRaisesRegex(ValidationError, "JVM metadata audit tests"):
            self.validate(lambda data: data["jobs"]["tests"].update({"steps": [
                step for step in data["jobs"]["tests"]["steps"]
                if step.get("run") != "python3 -m unittest discover -s tools/jvm/tests -v"
            ]}))

    def test_missing_python_startup_regressions_fail(self):
        for job in ("tests", "windows"):
            with self.subTest(job=job), self.assertRaisesRegex(ValidationError, "CPython startup"):
                self.validate(lambda data: data["jobs"][job].update({"steps": [
                    step for step in data["jobs"][job]["steps"] if "tools/python-runtime/tests" not in step.get("run", "")
                ]}))

    def test_windows_ci_regressions_cannot_be_removed(self):
        def remove(data):
            for step in data["jobs"]["windows"]["steps"]:
                if "tools/python-runtime/tests" in step.get("run", ""):
                    step["run"] = step["run"].replace("\npython -X utf8=0 -m unittest discover -s tools/ci/tests -v", "")
        with self.assertRaisesRegex(ValidationError, "CPython startup"):
            self.validate(remove)

    def test_native_startup_cannot_be_omitted_on_any_platform(self):
        for job in ("tests", "macos", "windows"):
            with self.subTest(job=job), self.assertRaisesRegex(ValidationError, "Native CPython startup"):
                self.validate(lambda data: data["jobs"][job].update({"steps": [
                    step for step in data["jobs"][job]["steps"] if "native_probe.py" not in step.get("run", "")
                ]}))

    def test_native_hash_regressions_cannot_be_removed(self):
        for job in ("tests", "windows"):
            with self.subTest(job=job), self.assertRaisesRegex(ValidationError, "Native hash regressions"):
                self.validate(lambda data: data["jobs"][job].update({"steps": [
                    step for step in data["jobs"][job]["steps"] if "tools/hashing/tests" not in step.get("run", "")
                ]}))

    def test_native_package_verification_cannot_be_omitted(self):
        for job in ("tests", "macos", "windows"):
            with self.subTest(job=job), self.assertRaisesRegex(ValidationError, "Native Python package verification"):
                self.validate(lambda data: data["jobs"][job].update({"steps": [
                    step for step in data["jobs"][job]["steps"] if "tools/python-package/package_probe.py" not in step.get("run", "")
                ]}))

    def test_missing_pull_request_trigger_fails(self):
        with self.assertRaisesRegex(ValidationError, "trigger"):
            self.validate(lambda data: data["on"].pop("pull_request"))

    def test_owned_stdlib_native_fixture_cannot_be_omitted(self):
        for job in ("tests", "macos", "windows"):
            with self.subTest(job=job), self.assertRaisesRegex(ValidationError, "Owned bootstrap stdlib"):
                self.validate(lambda data: data["jobs"][job].update({"steps": [
                    step for step in data["jobs"][job]["steps"] if "tools/python-stdlib/stdlib_probe.py" not in step.get("run", "")
                ]}))

    def test_bootstrap_stdlib_regressions_cannot_be_omitted(self):
        for job in ("tests", "windows"):
            with self.subTest(job=job), self.assertRaisesRegex(ValidationError, "Bootstrap stdlib regressions"):
                self.validate(lambda data: data["jobs"][job].update({"steps": [
                    step for step in data["jobs"][job]["steps"] if "tools/python-stdlib/tests" not in step.get("run", "")
                ]}))

    def test_path_filtered_workflow_fails(self):
        with self.assertRaisesRegex(ValidationError, "path filters"):
            self.validate(lambda data: data["on"]["pull_request"].update({"paths": ["src/**"]}))

    def test_masked_failure_fails(self):
        with self.assertRaisesRegex(ValidationError, "failures must propagate"):
            self.validate(lambda data: data["jobs"]["tests"].update({"continue-on-error": True}))

    def test_aggregate_must_run_after_failure(self):
        with self.assertRaisesRegex(ValidationError, "unsuccessful dependencies"):
            self.validate(lambda data: data["jobs"]["required"].pop("if"))

    def test_aggregate_must_cover_all_jobs(self):
        with self.assertRaisesRegex(ValidationError, "every check"):
            self.validate(lambda data: data["jobs"]["required"].update({"needs": ["repository"]}))

    def test_unconditional_success_cannot_replace_aggregate(self):
        with self.assertRaisesRegex(ValidationError, "selected results"):
            self.validate(lambda data: data["jobs"]["required"]["steps"][-1].update({"run": "true"}))

    def test_metadata_gate_cannot_replace_native_result_with_success(self):
        with self.assertRaisesRegex(ValidationError, "actual Windows service result"):
            self.validate(lambda data: data["jobs"]["metadata"]["steps"][-1].update({"run": "true"}))

    def test_metadata_gate_requires_windows_dependency(self):
        with self.assertRaisesRegex(ValidationError, "Windows service result"):
            self.validate(lambda data: data["jobs"]["metadata"].update({"needs": ["contracts"]}))

    def test_metadata_gate_must_evaluate_failed_windows_job(self):
        with self.assertRaisesRegex(ValidationError, "Windows service result"):
            self.validate(lambda data: data["jobs"]["metadata"].pop("if"))

    def test_metadata_gate_cannot_invent_dependency_results(self):
        with self.assertRaisesRegex(ValidationError, "actual Windows service result"):
            self.validate(lambda data: data["jobs"]["metadata"]["steps"][-1].update({"env": {"CI_NEEDS_JSON": "{}"}}))

    def test_write_permissions_fail(self):
        with self.assertRaisesRegex(ValidationError, "read-only"):
            self.validate(lambda data: data.update({"permissions": {"contents": "write"}}))

    def test_contract_job_cannot_omit_source_initialization(self):
        with self.assertRaisesRegex(ValidationError, "initialize pinned sources"):
            self.validate(lambda data: data["jobs"]["contracts"]["steps"].pop(1))

    def test_contract_job_cannot_omit_acceptance_checks(self):
        with self.assertRaisesRegex(ValidationError, "execute its checks"):
            self.validate(lambda data: data["jobs"]["contracts"]["steps"].pop(2))

    def test_contract_job_cannot_omit_regression_tests(self):
        with self.assertRaisesRegex(ValidationError, "execute its checks"):
            self.validate(lambda data: data["jobs"]["contracts"]["steps"].pop(3))

    def test_native_lanes_cannot_use_unspecified_runner_images(self):
        with self.assertRaisesRegex(ValidationError, "declared runner"):
            self.validate(lambda data: data["jobs"]["macos"].update({"runs-on": "macos-latest"}))

    def test_native_checks_cannot_be_removed(self):
        with self.assertRaisesRegex(ValidationError, "real native checks"):
            self.validate(lambda data: data["jobs"]["windows"]["steps"].pop(1))

    def test_native_packages_cannot_skip_dependency_producers(self):
        for target in ("macos", "windows", "linux"):
            with self.subTest(target=target), self.assertRaisesRegex(ValidationError, "wait for all verified"):
                self.validate(lambda data: data["jobs"][target].pop("needs"))

    def test_native_packages_require_same_run_artifact_access(self):
        for target in ("macos", "windows", "linux"):
            def remove(data):
                for step in data["jobs"][target]["steps"]:
                    if "native_job.py" in step.get("run", ""):
                        step["env"].pop("GITHUB_TOKEN")
            with self.subTest(target=target), self.assertRaisesRegex(ValidationError, "same-run artifact"):
                self.validate(remove)

    def test_windows_dependency_regressions_cannot_be_removed(self):
        with self.assertRaisesRegex(ValidationError, "Windows dependency regression tests"):
            self.validate(lambda data: data["jobs"]["windows"]["steps"].pop(2))

    def test_unicode_regressions_cannot_be_removed(self):
        def remove(data):
            data["jobs"]["tests"]["steps"] = [s for s in data["jobs"]["tests"]["steps"]
                                                 if s.get("name") != "Unicode source, oracle and cache regressions"]
        with self.assertRaisesRegex(ValidationError, "Unicode regressions"):
            self.validate(remove)

    def test_siphash_regressions_cannot_be_removed(self):
        def remove(data):
            data["jobs"]["tests"]["steps"] = [s for s in data["jobs"]["tests"]["steps"]
                                                 if s.get("name") != "SipHash source, oracle and cache regressions"]
        with self.assertRaisesRegex(ValidationError, "SipHash regressions"):
            self.validate(remove)

    def test_hashtable_regressions_cannot_be_removed(self):
        def remove(data):
            data["jobs"]["tests"]["steps"] = [s for s in data["jobs"]["tests"]["steps"]
                                                 if s.get("name") != "Hashtable source, oracle and cache regressions"]
        with self.assertRaisesRegex(ValidationError, "Hashtable regressions"):
            self.validate(remove)

    def test_remote_actions_cannot_replace_local_artifact_action(self):
        with self.assertRaisesRegex(ValidationError, "repository-owned action"):
            self.validate(lambda data: data["jobs"]["macos"]["steps"][-1].update({"uses": "external/action@main"}))

    def test_failure_report_upload_cannot_be_success_only(self):
        with self.assertRaisesRegex(ValidationError, "failure reports"):
            self.validate(lambda data: data["jobs"]["macos"]["steps"][-1].pop("if"))

    def test_artifact_identity_cannot_omit_commit(self):
        with self.assertRaisesRegex(ValidationError, "identify platform"):
            self.validate(lambda data: data["jobs"]["windows"]["steps"][-1]["with"].update({"name": "latest-build"}))

    def test_artifact_download_regressions_cannot_be_removed(self):
        def remove(data):
            for step in data["jobs"]["tests"]["steps"]:
                if step.get("name") == "Artifact transport regression tests":
                    step["run"] = "node --test tools/ci/tests/artifact_upload.test.mjs"
        with self.assertRaisesRegex(ValidationError, "artifact transport tests"):
            self.validate(remove)

    def test_dependency_matrix_cannot_be_replaced_by_a_constant_subset(self):
        with self.assertRaisesRegex(ValidationError, "complete selected matrix"):
            self.validate(lambda data: data["jobs"]["dependencies"]["strategy"].update({"matrix": {"component": ["ignored"]}}))

    def test_dependency_workflow_cannot_use_a_remote_reference(self):
        with self.assertRaisesRegex(ValidationError, "repository-owned reusable"):
            self.validate(lambda data: data["jobs"]["dependencies"].update({"uses": "other/repository/.github/workflows/build.yml@main"}))

    def test_dependency_build_failures_cannot_be_masked(self):
        with self.assertRaisesRegex(ValidationError, "failures must propagate"):
            self.validate(lambda data: data["jobs"]["dependencies"].update({"continue-on-error": True}))

    def test_dependency_matrix_cannot_use_a_change_filter_outside_the_validated_plan(self):
        with self.assertRaisesRegex(ValidationError, "matrix expansion"):
            self.validate(lambda data: data["jobs"]["dependencies"].update({"if": "false"}))

    def test_dependency_planner_cannot_be_replaced_by_a_success_stub(self):
        with self.assertRaisesRegex(ValidationError, "real source manifests"):
            self.validate(lambda data: data["jobs"]["dependency-plan"]["steps"][-1].update({"run": "true"}))

    def test_reusable_dependency_job_cannot_be_unbounded(self):
        path = self.root / ".github/workflows/dependency-build.yml"
        content = json.loads(path.read_text(encoding="utf-8"))
        content["jobs"]["build"].pop("timeout-minutes")
        path.write_text(json.dumps(content) + "\n")
        with self.assertRaisesRegex(ValidationError, "bounded timeout"):
            self.validate(lambda data: None)


if __name__ == "__main__":
    unittest.main()
