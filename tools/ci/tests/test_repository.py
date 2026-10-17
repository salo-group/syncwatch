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
        path.write_text(content, encoding="utf-8")
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
        for source in (REPOSITORY / "packages/design").rglob("*.json"):
            self.write(str(source.relative_to(REPOSITORY)), source.read_text())

    def test_missing_token_reference_fails(self):
        self.copy_design()
        path = self.root / "packages/design/components.json"
        data = json.loads(path.read_text())
        data["components"]["LibraryLayout"]["tokens"].append("unknown.token")
        path.write_text(json.dumps(data) + "\n")
        with self.assertRaisesRegex(ValidationError, "unknown token"):
            check_design(self.root)

    def test_invalid_geometry_fails(self):
        self.copy_design()
        path = self.root / "packages/design/tokens/geometry.json"
        data = json.loads(path.read_text())
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
        path.write_text(path.read_text() + "\tbranch = main\n")
        with self.assertRaisesRegex(ValidationError, "floating branches"):
            check_submodules(self.root)

    def test_copied_vendor_source_fails(self):
        self.initialize_submodule_fixture()
        self.write("3rdparty/copied/source.py", "value = 1\n")
        subprocess.run(["git", "add", "3rdparty/copied/source.py"], cwd=self.root, check=True)
        with self.assertRaisesRegex(ValidationError, "must be a Git submodule"):
            check_submodules(self.root)

    def copy_native_manifests(self):
        files = ["global.json", "tools/native/toolchains.json", "NuGet.Config", "SyncWatch.slnx"]
        solution = ET.parse(REPOSITORY / "SyncWatch.slnx").getroot()
        files += [project.attrib["Path"] for project in solution.findall(".//Project")]
        for name in files:
            self.write(name, (REPOSITORY / name).read_text())

    def test_native_sdk_roll_forward_fails(self):
        self.copy_native_manifests()
        path = self.root / "global.json"
        data = json.loads(path.read_text())
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

    def test_native_project_reference_cannot_escape_repository(self):
        self.copy_native_manifests()
        path = self.root / "apps/windows/SyncWatch/SyncWatch.csproj"
        content = ET.parse(path)
        content.find(".//ProjectReference").set("Include", "../../../../outside.csproj")
        content.write(path)
        with self.assertRaisesRegex(ValidationError, "stay local"):
            check_native_manifests(self.root)


class WorkflowChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / ".github/workflows/repository.yml"
        self.path.parent.mkdir(parents=True)
        self.workflow = json.loads((REPOSITORY / ".github/workflows/repository.yml").read_text())

    def validate(self, mutate):
        workflow = copy.deepcopy(self.workflow)
        mutate(workflow)
        self.path.write_text(json.dumps(workflow) + "\n")
        check_workflows(self.root)

    def test_missing_pull_request_trigger_fails(self):
        with self.assertRaisesRegex(ValidationError, "trigger"):
            self.validate(lambda data: data["on"].pop("pull_request"))

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


if __name__ == "__main__":
    unittest.main()
