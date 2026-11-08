import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build


REPOSITORY = Path(__file__).resolve().parents[3]
TARGET = next(name for name, (system, architectures, _) in build.PLATFORMS.items() if platform.system() == system and platform.machine() in architectures)


class DependencyBuildTests(unittest.TestCase):
    def test_original_blob_export_ignores_upstream_eol_conversion(self):
        source = self.root / "3rdparty/example"
        (source / ".gitattributes").write_text("*.props text eol=crlf\n", newline="\n")
        (source / "build.props").write_bytes(b"original\nsource\n")
        self.commit(source)
        revision = build.git(source, "rev-parse", "HEAD").decode().strip()
        entries = build.exported_entries(source, ["LICENSE", "build.props"])
        archive = self.root / "original.tar"
        build.export_original_blobs(source, archive, entries, revision)
        build.verify_source_export(archive, entries, revision, ["LICENSE"])
        with tarfile.open(archive) as package:
            self.assertEqual(package.extractfile("build.props").read(), b"original\nsource\n")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "config", "--local", "core.autocrlf", "false"], cwd=self.root, check=True)
        for prefix in build.FRAMEWORK_INPUTS:
            source, destination = REPOSITORY / prefix, self.root / prefix
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__"))
            else:
                shutil.copyfile(source, destination)
        source = self.root / "3rdparty/example"
        source.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        subprocess.run(["git", "config", "--local", "core.autocrlf", "false"], cwd=source, check=True)
        (source / "LICENSE").write_text("Fixture source license\n", newline="\n")
        (source / "library.txt").write_text("source payload\n", newline="\n")
        (source / ".gitignore").write_text("ignored.txt\n", newline="\n")
        self.commit(source)
        revision = build.git(source, "rev-parse", "HEAD").decode().strip()
        (self.root / ".gitmodules").write_text('[submodule "example"]\n path = 3rdparty/example\n url = https://example.invalid/source.git\n', newline="\n")
        subprocess.run(["git", "update-index", "--add", "--cacheinfo", "160000", revision, "3rdparty/example"], cwd=self.root, check=True)
        (self.root / ".gitignore").write_text("artifacts/\n.local/\n__pycache__/\n", newline="\n")
        self.recipe = self.root / "recipe.py"
        self.recipe.write_text(
            "import os\nfrom pathlib import Path\n"
            "assert os.environ['PIP_NO_INDEX'] == '1'\n"
            "assert os.environ['npm_config_offline'] == 'true'\n"
            "assert 'GITHUB_TOKEN' not in os.environ and 'ACTIONS_RUNTIME_TOKEN' not in os.environ\n"
            "output = Path(os.environ['SYNCWATCH_DEPENDENCY_OUTPUT'])\n"
            "sources = Path(os.environ['SYNCWATCH_DEPENDENCY_SOURCES'])\n"
            "assert not (sources / '3rdparty/example/ignored.txt').exists()\n"
            "(output / 'library.txt').write_bytes((sources / '3rdparty/example/library.txt').read_bytes())\n"
            "counter = Path('artifacts/counter')\n"
            "counter.write_text(str(int(counter.read_text()) + 1) if counter.exists() else '1')\n"
        , newline="\n")
        self.component = {
            "status": "ready", "always": True, "roots": ["3rdparty/example"],
            "sources": {"3rdparty/example": {"revision": revision, "license": "Apache-2.0", "notices": ["LICENSE"], "requires": [], "unusedSubmodules": {}}},
            "inputs": ["recipe.py"], "watch": ["recipe.py"],
            "targets": {TARGET: {
                "toolchains": {"fixture": {"command": ["{python}", "-c", "print('fixture-compiler-v1')"], "pattern": "^fixture-compiler-v[0-9]+$"}},
                "environment": {}, "flags": {"configuration": "release"},
                "command": ["{python}", "recipe.py"], "timeoutSeconds": 10,
            }},
        }
        self.manifest = {"schemaVersion": 1, "components": {"example": self.component}}
        self.write_manifest()
        self.commit(self.root)
        self.environment = patch.dict(os.environ, {"GITHUB_ACTIONS": "false", "GITHUB_EVENT_NAME": "", "GITHUB_REF": "", "GITHUB_TOKEN": "private-fixture-token", "ACTIONS_RUNTIME_TOKEN": "private-runtime-token"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    @staticmethod
    def commit(root):
        subprocess.run(["git", "add", "--sparse", "."], cwd=root, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "Fixture"], cwd=root, check=True)

    def write_manifest(self):
        (self.root / "tools/dependencies/components.json").write_text(json.dumps(self.manifest, indent=2) + "\n", newline="\n")

    def prepare(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return build.prepare(self.root, "example", TARGET)

    def plan_cli(self, event, kind="pull_request", ref=""):
        metadata = self.root / "artifacts/planning"
        metadata.mkdir(parents=True, exist_ok=True)
        event_path = metadata / "event.json"
        event_path.write_text(json.dumps(event), encoding="utf-8")
        environment = dict(os.environ, GITHUB_EVENT_NAME=kind, GITHUB_EVENT_PATH=str(event_path), GITHUB_REF=ref,
                           GITHUB_OUTPUT=str(metadata / "outputs"), GITHUB_STEP_SUMMARY=str(metadata / "summary"))
        return subprocess.run([sys.executable, "tools/dependencies/build.py", "plan"], cwd=self.root,
                              env=environment, capture_output=True, text=True)

    def test_plan_uses_full_validation_for_code_assets_and_mixed_changes(self):
        self.manifest["components"]["fastjsonschema"] = copy.deepcopy(self.component)
        self.write_manifest()
        self.commit(self.root)
        for name, mode in (("README.md", "documentation"), ("docs/guide.md", "documentation"),
                           ("docs/image.svg", "full"), ("apps/macos/Main.swift", "full"),
                           ("apps/windows/Main.cs", "full"), ("apps/linux/Main.kt", "full"),
                           ("contracts/example.json", "full"), ("packages/design/example.json", "full"),
                           ("tools/ci/example.py", "full"), (".github/workflows/example.yml", "full")):
            with self.subTest(path=name):
                base = build.git(self.root, "rev-parse", "HEAD").decode().strip()
                path = self.root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("Fixture change\n", encoding="utf-8")
                if mode == "full":
                    (self.root / "docs/guide.md").write_text(name + "\n", encoding="utf-8")
                self.commit(self.root)
                result = self.plan_cli({"pull_request": {"base": {"sha": base}}})
                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(report["mode"], mode)
                expected = {"fastjsonschema"} if mode == "documentation" else {"example", "fastjsonschema"}
                self.assertEqual({entry["component"] for entry in report["matrix"]["include"]}, expected)

    def test_plan_cannot_skip_native_validation_for_unknown_history_or_forced_events(self):
        self.manifest["components"]["fastjsonschema"] = copy.deepcopy(self.component)
        self.write_manifest()
        self.commit(self.root)
        base = build.git(self.root, "rev-parse", "HEAD").decode().strip()
        (self.root / "docs").mkdir()
        (self.root / "docs/guide.md").write_text("# Guide\n", encoding="utf-8")
        self.commit(self.root)
        head = build.git(self.root, "rev-parse", "HEAD").decode().strip()
        for kind, comparison, inputs, ref, expected in (
            ("push", base, {}, "refs/heads/main", "documentation"),
            ("push", base, {}, "refs/heads/topic", "full"),
            ("workflow_dispatch", base, {"cold": False}, "", "full"),
            ("workflow_dispatch", base, {"cold": True}, "", "full"),
            ("schedule", base, {}, "", "full"), ("merge_group", base, {}, "", "full"),
            ("pull_request", "f" * 40, {}, "", "full"), ("pull_request", "0" * 40, {}, "", "full"),
            ("pull_request", 42, {}, "", "full"), ("pull_request", None, {}, "", "full"),
            ("pull_request", head, {}, "", "full"),
        ):
            with self.subTest(kind=kind, comparison=comparison, inputs=inputs, ref=ref):
                result = self.plan_cli({"before": comparison, "inputs": inputs}, kind, ref)
                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(report["mode"], expected)
                self.assertEqual({entry["component"] for entry in report["matrix"]["include"]},
                                 {"fastjsonschema"} if expected == "documentation" else {"example", "fastjsonschema"})

    def test_documentation_plan_keeps_only_the_verified_validator_baseline(self):
        self.manifest["components"]["fastjsonschema"] = copy.deepcopy(self.component)
        self.write_manifest()
        self.commit(self.root)
        base = build.git(self.root, "rev-parse", "HEAD").decode().strip()
        (self.root / "docs").mkdir()
        (self.root / "docs/application.md").write_text("# Application\n", encoding="utf-8")
        self.commit(self.root)
        event = self.root / "event.json"
        event.write_text(json.dumps({"pull_request": {"base": {"sha": base}}}), encoding="utf-8")
        environment = dict(os.environ, GITHUB_EVENT_NAME="pull_request", GITHUB_EVENT_PATH=str(event),
                           GITHUB_OUTPUT=str(self.root / "outputs"), GITHUB_STEP_SUMMARY=str(self.root / "summary"))
        result = subprocess.run([sys.executable, "tools/dependencies/build.py", "plan"], cwd=self.root,
                                env=environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["mode"], "documentation")
        self.assertEqual(report["matrix"], {"include": [{"component": "fastjsonschema", "platform": TARGET, "cold": False}]})
        self.assertIn("mode=documentation\n", (self.root / "outputs").read_text())

    def test_markdown_declared_as_a_component_input_requires_full_validation(self):
        self.manifest["components"]["fastjsonschema"] = copy.deepcopy(self.component)
        self.component["watch"] = ["docs/application.md"]
        self.write_manifest()
        self.commit(self.root)
        base = build.git(self.root, "rev-parse", "HEAD").decode().strip()
        (self.root / "docs").mkdir()
        (self.root / "docs/application.md").write_text("# Input consumed by a component\n", encoding="utf-8")
        self.commit(self.root)
        event = self.root / "event.json"
        event.write_text(json.dumps({"pull_request": {"base": {"sha": base}}}), encoding="utf-8")
        environment = dict(os.environ, GITHUB_EVENT_NAME="pull_request", GITHUB_EVENT_PATH=str(event),
                           GITHUB_OUTPUT=str(self.root / "outputs"), GITHUB_STEP_SUMMARY=str(self.root / "summary"))
        result = subprocess.run([sys.executable, "tools/dependencies/build.py", "plan"], cwd=self.root,
                                env=environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["mode"], "full")

    def test_markdown_symlink_requires_full_validation_without_host_symlink_privileges(self):
        self.manifest["components"]["fastjsonschema"] = copy.deepcopy(self.component)
        self.write_manifest()
        self.commit(self.root)
        base = build.git(self.root, "rev-parse", "HEAD").decode().strip()
        blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=self.root, input="../recipe.py",
                              capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(["git", "update-index", "--add", "--cacheinfo", "120000", blob, "docs/link.md"], cwd=self.root, check=True)
        subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "Fixture link"], cwd=self.root, check=True)
        event = self.root / "event.json"
        event.write_text(json.dumps({"pull_request": {"base": {"sha": base}}}), encoding="utf-8")
        result = subprocess.run([sys.executable, "tools/dependencies/build.py", "plan"], cwd=self.root,
                                env=dict(os.environ, GITHUB_EVENT_NAME="pull_request", GITHUB_EVENT_PATH=str(event)),
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["mode"], "full")

    def test_nonportable_markdown_names_require_full_validation(self):
        self.manifest["components"]["fastjsonschema"] = copy.deepcopy(self.component)
        self.write_manifest()
        self.commit(self.root)
        blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=self.root, input="# Guide\n",
                              capture_output=True, text=True, check=True).stdout.strip()
        # Index-only malformed fixtures never materialize these names on the host.
        for name in ("docs/CON.md", "docs/guide:extra.md", "docs/guide\t.md", "docs/guide|.md", 'docs/guide".md',
                     "docs/<guide>.md", "docs/aux/file.md", "docs/trailing./guide.md"):
            with self.subTest(name=name):
                base = build.git(self.root, "rev-parse", "HEAD").decode().strip()
                subprocess.run(["git", "-c", "core.protectNTFS=false", "update-index", "--add", "--cacheinfo", "100644", blob, name],
                               cwd=self.root, check=True)
                subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "Malformed name fixture"],
                               cwd=self.root, check=True)
                result = self.plan_cli({"pull_request": {"base": {"sha": base}}})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["mode"], "full")

    def test_sparse_index_materialization_is_separate_from_exact_source_exports(self):
        source = self.root / "3rdparty/example"
        for directory in ("client", "server"):
            (source / directory).mkdir()
            (source / directory / "library.c").write_text("/* original " + directory + " */\n", newline="\n")
        self.commit(source)
        revision = build.git(source, "rev-parse", "HEAD").decode().strip()
        specification = {"revision": revision, "exports": ["LICENSE", "client", "server"],
                         "notices": ["LICENSE"], "checkoutDirectories": ["client"]}
        build.initialize_export_source(self.root, "3rdparty/example", specification, "https://example.invalid/source.git")
        self.assertTrue((source / "client/library.c").is_file())
        self.assertFalse((source / "server/library.c").exists())
        self.assertFalse(build.git(source, "status", "--porcelain").strip())
        archive = self.root / "originals.tar"
        entries = build.exported_entries(source, build.export_paths(specification))
        build.export_original_blobs(source, archive, entries, revision)
        build.verify_source_export(archive, entries, revision, ["LICENSE"])
        with tarfile.open(archive) as package:
            self.assertEqual(package.extractfile("server/library.c").read(), b"/* original server */\n")

    def test_sparse_index_directory_cannot_be_a_file(self):
        source = self.root / "3rdparty/example"
        specification = {"revision": build.git(source, "rev-parse", "HEAD").decode().strip(),
                         "exports": ["LICENSE"], "notices": ["LICENSE"], "checkoutDirectories": ["LICENSE"]}
        with self.assertRaisesRegex(ValueError, "original directories"):
            build.initialize_export_source(self.root, "3rdparty/example", specification, "https://example.invalid/source.git")

    def remove_source_fixture(self, source):
        """Remove an owned Git fixture, including read-only Windows objects."""
        self.assertTrue(source.resolve().is_relative_to(self.root.resolve()))
        def retry_readonly(operation, path, error):
            if os.name != "nt" or not isinstance(error, PermissionError):
                raise error
            os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
            operation(path)
        shutil.rmtree(source, onexc=retry_readonly)

    def run_build(self, cold=False):
        with contextlib.redirect_stdout(io.StringIO()):
            return build.build(self.root, "example", TARGET, cold)

    def test_real_cold_build_then_verified_reuse_and_forced_cold_rebuild(self):
        first = self.run_build()
        self.assertFalse(first["cacheHit"])
        self.assertEqual((self.root / "artifacts/counter").read_text(), "1")
        second = self.run_build()
        self.assertTrue(second["cacheHit"])
        self.assertEqual(first["key"], second["key"])
        self.assertEqual(first["files"], second["files"])
        self.assertEqual((self.root / "artifacts/counter").read_text(), "1")
        third = self.run_build(cold=True)
        self.assertFalse(third["cacheHit"])
        self.assertEqual(first["files"], third["files"])
        self.assertEqual((self.root / "artifacts/counter").read_text(), "2")

    def add_verifier(self):
        path = self.root / "verify.py"
        path.write_text("from pathlib import Path\n"
                        f"payload = Path('artifacts/dependencies/example/{TARGET}/payload')\n"
                        "assert (payload/'notices/example/LICENSE').read_text() == 'Fixture source license\\n'\n"
                        "counter = Path('artifacts/verifications')\n"
                        "counter.write_text(str(int(counter.read_text())+1) if counter.exists() else '1')\n", newline="\n")
        self.component["inputs"].append("verify.py")
        self.component["targets"][TARGET]["verifyCommand"] = ["{python}", "verify.py"]
        self.write_manifest()
        subprocess.run(["git", "add", "verify.py"], cwd=self.root, check=True)

    def test_component_verifier_runs_on_cold_and_cached_payloads(self):
        self.add_verifier()
        self.run_build(cold=True)
        self.assertEqual((self.root / "artifacts/verifications").read_text(), "1")
        self.assertTrue(self.run_build()["cacheHit"])
        self.assertEqual((self.root / "artifacts/verifications").read_text(), "2")

    def test_ci_cli_reuses_verified_source_archives_and_runs_each_fresh_verifier(self):
        self.set_exports(["LICENSE", "library.txt"])
        self.add_verifier()
        self.prepare()
        archive = self.root / f"artifacts/dependencies/example/{TARGET}/example.tar"
        original = archive.read_bytes()
        timestamp = 1600000000000000000
        os.utime(archive, ns=(timestamp, timestamp))
        command = [sys.executable, "tools/dependencies/build.py", "build", "--component", "example",
                   "--platform", TARGET, "--reuse-prepared-sources"]
        for count in (1, 2):
            result = subprocess.run(command, cwd=self.root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(archive.read_bytes(), original)
            self.assertEqual(archive.stat().st_mtime_ns, timestamp)
            self.assertEqual((self.root / "artifacts/verifications").read_text(), str(count))
            self.assertEqual((self.root / "artifacts/counter").read_text(), "1")

    def prepared_build_cli(self, *flags):
        return subprocess.run([sys.executable, "tools/dependencies/build.py", "build", "--component", "example",
                               "--platform", TARGET, "--reuse-prepared-sources", *flags],
                              cwd=self.root, capture_output=True, text=True)

    def test_prepared_source_corruption_fails_before_the_build_command(self):
        self.set_exports(["LICENSE", "library.txt"])
        self.prepare()
        archive = self.root / f"artifacts/dependencies/example/{TARGET}/example.tar"
        archive.write_bytes(archive.read_bytes().replace(b"source payload\n", b"forged payload\n"))
        result = self.prepared_build_cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Dependency validation/build failed", result.stderr)
        self.assertFalse((self.root / "artifacts/counter").exists())

    def test_prepared_sources_cannot_hide_a_dirty_source_checkout(self):
        self.set_exports(["LICENSE", "library.txt"])
        self.prepare()
        (self.root / "3rdparty/example/library.txt").write_text("changed source\n", newline="\n")
        result = self.prepared_build_cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Dependency validation/build failed", result.stderr)
        self.assertFalse((self.root / "artifacts/counter").exists())

    def test_cold_cli_recreates_sources_instead_of_reading_prepared_archives(self):
        self.set_exports(["LICENSE", "library.txt"])
        self.prepare()
        archive = self.root / f"artifacts/dependencies/example/{TARGET}/example.tar"
        original = archive.read_bytes()
        archive.write_bytes(b"invalid prepared archive")
        result = self.prepared_build_cli("--cold")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(archive.read_bytes(), original)
        self.assertEqual((self.root / "artifacts/counter").read_text(), "1")

    def test_prepared_source_reuse_still_discovers_changed_toolchains(self):
        self.set_exports(["LICENSE", "library.txt"])
        self.prepare()
        first = self.prepared_build_cli()
        self.assertEqual(first.returncode, 0, first.stderr)
        request = self.root / f"artifacts/dependencies/example/{TARGET}/request.json"
        before = json.loads(request.read_text())["key"]
        self.component["targets"][TARGET]["toolchains"]["fixture"]["command"][-1] = "print('fixture-compiler-v2')"
        self.write_manifest()
        second = self.prepared_build_cli()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertNotEqual(json.loads(request.read_text())["key"], before)
        self.assertEqual((self.root / "artifacts/counter").read_text(), "2")

    def git_cache_cli(self, operation, *flags):
        return subprocess.run([sys.executable, str(REPOSITORY / "tools/ci/git_cache.py"), operation,
                               "--root", str(self.root), "--component", "example", "--platform", TARGET, *flags],
                              cwd=self.root, capture_output=True, text=True)

    def test_git_object_cache_restores_exact_sources_without_the_remote(self):
        self.set_exports(["LICENSE", "library.txt"])
        before = self.prepare()
        staged = self.git_cache_cli("stage")
        self.assertEqual(staged.returncode, 0, staged.stderr)
        source = self.root / "3rdparty/example"
        self.remove_source_fixture(source)
        restored = self.git_cache_cli("restore")
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertIn('"hit": true', restored.stdout)
        self.assertEqual((source / "library.txt").read_bytes(), b"source payload\n")
        self.assertEqual(self.prepare()["inputs"]["sources"], before["inputs"]["sources"])

    def change_git_cache(self, name, contents):
        plan = self.git_cache_cli("plan")
        self.assertEqual(plan.returncode, 0, plan.stderr)
        archive = self.root / json.loads(plan.stdout)["archive"]
        with zipfile.ZipFile(archive) as package:
            entries = {entry.filename: package.read(entry) for entry in package.infolist()}
        manifest = json.loads(entries["build-manifest.json"])
        if name is None:
            name = next(iter(manifest["files"]))
        entries[name] = contents
        manifest["files"][name] = {"sha256": hashlib.sha256(contents).hexdigest(), "mode": 0o644}
        entries["build-manifest.json"] = json.dumps(manifest).encode()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
            for name, data in entries.items():
                package.writestr(name, data)

    def test_git_object_cache_rejects_corrupt_objects_before_installing_sources(self):
        self.set_exports(["LICENSE", "library.txt"])
        self.prepare()
        staged = self.git_cache_cli("stage")
        self.assertEqual(staged.returncode, 0, staged.stderr)
        self.change_git_cache(None, b"invalid Git object")
        source = self.root / "3rdparty/example"
        self.remove_source_fixture(source)
        result = self.git_cache_cli("restore")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(source.exists())

    def test_git_object_cache_cannot_import_alternates_configuration_or_hooks(self):
        self.set_exports(["LICENSE", "library.txt"])
        self.prepare()
        source = self.root / "3rdparty/example"
        subprocess.run(["git", "config", "--local", "core.hooksPath", "/fixture/producer-only-hooks"], cwd=source, check=True)
        staged = self.git_cache_cli("stage")
        self.assertEqual(staged.returncode, 0, staged.stderr)
        self.remove_source_fixture(source)
        restored = self.git_cache_cli("restore")
        self.assertEqual(restored.returncode, 0, restored.stderr)
        hooks = subprocess.run(["git", "config", "--local", "--get", "core.hooksPath"], cwd=source, capture_output=True)
        self.assertNotEqual(hooks.returncode, 0)
        self.remove_source_fixture(source)
        self.change_git_cache("payload/0/info/alternates", b"/fixture/external-object-database")
        rejected = self.git_cache_cli("restore")
        self.assertNotEqual(rejected.returncode, 0)
        self.assertFalse(source.exists())

    def test_git_object_cache_cold_requests_do_not_restore_sources(self):
        self.set_exports(["LICENSE", "library.txt"])
        self.prepare()
        staged = self.git_cache_cli("stage")
        self.assertEqual(staged.returncode, 0, staged.stderr)
        source = self.root / "3rdparty/example"
        self.remove_source_fixture(source)
        restored = self.git_cache_cli("restore", "--cold")
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(json.loads(restored.stdout), {"hit": False, "restored": 0})
        self.assertFalse(source.exists())

    def test_git_object_cache_does_not_reuse_a_previous_source_pin(self):
        self.set_exports(["LICENSE", "library.txt"])
        self.prepare()
        staged = self.git_cache_cli("stage")
        self.assertEqual(staged.returncode, 0, staged.stderr)
        source = self.root / "3rdparty/example"
        (source / "library.txt").write_text("updated pinned source\n", newline="\n")
        self.update_source_pin()
        self.remove_source_fixture(source)
        restored = self.git_cache_cli("restore")
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(json.loads(restored.stdout), {"hit": False, "restored": 0})
        self.assertFalse(source.exists())

    def test_git_object_cache_preserves_an_existing_source_checkout(self):
        self.set_exports(["LICENSE", "library.txt"])
        self.prepare()
        staged = self.git_cache_cli("stage")
        self.assertEqual(staged.returncode, 0, staged.stderr)
        source = self.root / "3rdparty/example"
        (source / "library.txt").write_text("existing local change\n", newline="\n")
        restored = self.git_cache_cli("restore")
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(json.loads(restored.stdout), {"hit": True, "restored": 0})
        self.assertEqual((source / "library.txt").read_text(), "existing local change\n")

    def test_git_object_cache_restores_selected_long_paths(self):
        source = self.root / "3rdparty/example"
        subprocess.run(["git", "config", "--local", "core.longpaths", "true"], cwd=source, check=True)
        name = "/".join("directory" + str(index) + "x" * 24 for index in range(8)) + "/selected.txt"
        original = source / name
        original.parent.mkdir(parents=True)
        original.write_bytes(b"original long source path\n")
        self.update_source_pin()
        self.set_exports(sorted(["LICENSE", "library.txt", name]))
        self.prepare()
        staged = self.git_cache_cli("stage")
        self.assertEqual(staged.returncode, 0, staged.stderr)
        self.remove_source_fixture(source)
        restored = self.git_cache_cli("restore")
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual((source / name).read_bytes(), b"original long source path\n")

    def test_git_object_cache_round_trip_accepts_shallow_partial_clones(self):
        source = self.root / "3rdparty/example"
        (source / "unselected.txt").write_bytes(b"unselected original source\n" * 100)
        self.update_source_pin()
        self.set_exports(["LICENSE", "library.txt"])
        remote = self.root / "remote.git"
        subprocess.run(["git", "clone", "-q", "--bare", str(source), str(remote)], check=True)
        subprocess.run(["git", "config", "uploadpack.allowFilter", "true"], cwd=remote, check=True)
        self.remove_source_fixture(source)
        rewrite = {"GIT_ALLOW_PROTOCOL": "file", "GIT_CONFIG_COUNT": "1",
                   "GIT_CONFIG_KEY_0": "url." + remote.as_uri() + ".insteadOf",
                   "GIT_CONFIG_VALUE_0": "https://example.invalid/source.git"}
        with patch.dict(os.environ, rewrite):
            before = self.prepare()
        missing = build.git(source, "rev-parse", "HEAD:unselected.txt").decode().strip()
        probe = subprocess.run(["git", "cat-file", "-e", missing], cwd=source,
                               env=dict(os.environ, GIT_NO_LAZY_FETCH="1"), capture_output=True)
        self.assertNotEqual(probe.returncode, 0, "Fixture must retain an uncached promisor blob")
        staged = self.git_cache_cli("stage")
        self.assertEqual(staged.returncode, 0, staged.stderr)
        self.remove_source_fixture(source)
        restored = self.git_cache_cli("restore")
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(self.prepare()["inputs"]["sources"], before["inputs"]["sources"])

    def separate_consumer(self):
        self.add_verifier()
        directory = self.root / "consumer"
        directory.mkdir()
        verifier = directory / "check.py"
        (self.root / "verify.py").rename(verifier)
        self.component["inputs"].remove("verify.py")
        self.component["verificationInputs"] = ["consumer"]
        self.component["targets"][TARGET]["verifyCommand"] = ["{python}", "consumer/check.py"]
        self.write_manifest()
        self.commit(self.root)
        return verifier

    def test_consumer_changes_reuse_sdk_but_run_fresh_checks_with_current_provenance(self):
        verifier = self.separate_consumer()
        first = self.run_build()
        verifier.write_text(verifier.read_text() + "\n# New application revision\n", newline="\n")
        self.commit(self.root)
        second = self.run_build()
        self.assertTrue(second["cacheHit"])
        self.assertEqual(first["key"], second["key"])
        self.assertEqual(first["files"], second["files"])
        self.assertNotEqual(first["verificationInputs"], second["verificationInputs"])
        self.assertEqual(second["buildSourceCommit"], first["buildSourceCommit"])
        self.assertNotEqual(second["verifiedSourceCommit"], second["buildSourceCommit"])
        self.assertEqual((self.root / "artifacts/counter").read_text(), "1")
        self.assertEqual((self.root / "artifacts/verifications").read_text(), "2")

        # New local application sources are recorded even before staging them.
        (self.root / "consumer/new.py").write_text("# New source\n", newline="\n")
        third = self.run_build()
        self.assertTrue(third["cacheHit"])
        self.assertEqual(third["key"], first["key"])
        self.assertIn("consumer/new.py", third["verificationInputs"])
        self.assertTrue(third["verifiedLocalChanges"])

        verifier.write_text(verifier.read_text() + "\nraise SystemExit(7)\n", newline="\n")
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_build()
        with zipfile.ZipFile(self.root / f"artifacts/dependencies/example/{TARGET}/artifact.zip") as archive:
            failure = json.loads(archive.read("build-manifest.json"))
            self.assertEqual(failure["result"], "failure")
            self.assertTrue(failure["cacheHit"])
            self.assertNotEqual(failure["verificationInputs"], third["verificationInputs"])
            self.assertEqual(archive.namelist(), ["build-manifest.json"])
        self.assertEqual((self.root / "artifacts/counter").read_text(), "1")

        # A failed consumer neither succeeds from cache nor destroys the verified SDK cache.
        verifier.write_text(verifier.read_text().replace("raise SystemExit(7)", "# Corrected consumer"), newline="\n")
        self.assertTrue(self.run_build()["cacheHit"])
        self.recipe.write_text(self.recipe.read_text() + "\n# Changed build recipe\n", newline="\n")
        rebuilt = self.run_build()
        self.assertFalse(rebuilt["cacheHit"])
        self.assertNotEqual(rebuilt["key"], first["key"])
        self.assertEqual((self.root / "artifacts/counter").read_text(), "2")

    def test_verifier_cannot_change_the_inputs_it_claims_to_have_checked(self):
        verifier = self.separate_consumer()
        verifier.write_text(verifier.read_text() + "\nsource=Path(__file__)\nsource.write_text(source.read_text()+'\\n# mutation\\n')\n", newline="\n")
        with self.assertRaisesRegex(ValueError, "Verification changed"):
            self.run_build()
        self.assertEqual(list((self.root / f"artifacts/dependencies/example/{TARGET}/payload").iterdir()), [])

    def test_consumer_cannot_modify_or_poison_the_sdk_under_an_unchanged_artifact_key(self):
        verifier = self.separate_consumer()
        first = self.run_build()
        request = self.prepare()
        original = verifier.read_text()
        for target in ("library.txt", "unexpected-report.txt", "replace-directory"):
            with self.subTest(target=target):
                mutation = ("import shutil\nshutil.rmtree(payload)\npayload.write_text('consumer mutation')\n"
                            if target == "replace-directory" else f"(payload/{target!r}).write_text('consumer mutation')\n")
                verifier.write_text(original + "\n" + mutation, newline="\n")
                with self.assertRaisesRegex(ValueError, "Verification changed the SDK payload"):
                    self.run_build()
                with zipfile.ZipFile(request["cacheFile"]) as cache:
                    self.assertEqual(cache.read("payload/library.txt"), b"source payload\n")
                    self.assertNotIn("payload/unexpected-report.txt", cache.namelist())
        with self.assertRaisesRegex(ValueError, "Verification changed the SDK payload"):
            self.run_build(cold=True)
        verifier.write_text(original, newline="\n")
        recovered = self.run_build()
        self.assertTrue(recovered["cacheHit"])
        self.assertEqual(recovered["key"], first["key"])

    def test_consumer_input_contract_requires_separate_paths_and_fresh_verification(self):
        original = copy.deepcopy(self.component)
        for field, value, message in [
            ("verificationInputs", ["recipe.py"], "must not overlap"),
            ("verificationInputs", ["consumer"], "fresh verifier"),
            ("verificationInputs", [], "must be explicit"),
            ("verificationInputs", ["../foreign"], "Unsafe"),
        ]:
            with self.subTest(value=value):
                self.manifest["components"]["example"] = copy.deepcopy(original)
                self.manifest["components"]["example"][field] = value
                self.write_manifest()
                with self.assertRaisesRegex(ValueError, message):
                    build.registry(self.root)

    def test_consumer_changes_select_the_component_without_polluting_sdk_identity(self):
        self.separate_consumer()
        self.component["always"] = False
        matrix, _ = build.selected({"example": self.component}, ["consumer/check.py"])
        self.assertEqual(matrix["include"], [{"component": "example", "platform": TARGET, "cold": False}])

    def test_rewritten_cache_manifest_cannot_hide_missing_license_from_verifier(self):
        self.add_verifier();self.run_build(cold=True);request = self.prepare()
        with zipfile.ZipFile(request["cacheFile"]) as archive:members = {n: archive.read(n) for n in archive.namelist()}
        name = "payload/notices/example/LICENSE";members[name] = b"wrong license"
        manifest = json.loads(members["build-manifest.json"])
        import hashlib
        manifest["files"][name]["sha256"] = hashlib.sha256(members[name]).hexdigest()
        members["build-manifest.json"] = json.dumps(manifest).encode()
        with zipfile.ZipFile(request["cacheFile"], "w") as archive:
            for name, data in members.items():archive.writestr(name, data)
        with self.assertRaises(subprocess.CalledProcessError):self.run_build()
        with zipfile.ZipFile(request["archive"]) as archive:
            self.assertEqual(archive.namelist(), ["build-manifest.json"])
            self.assertEqual(json.loads(archive.read("build-manifest.json"))["result"], "failure")

    def test_invalid_verifier_command_is_rejected(self):
        self.component["targets"][TARGET]["verifyCommand"] = []
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "Commands must be nonempty"):
            self.prepare()

    def test_corrupt_cache_fails_and_retains_no_success_payload(self):
        self.run_build()
        request = self.prepare()
        with zipfile.ZipFile(request["cacheFile"]) as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        members["payload/library.txt"] = b"corrupt bytes"
        with zipfile.ZipFile(request["cacheFile"], "w") as archive:
            for name, data in members.items():
                archive.writestr(name, data)
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.run_build()
        with zipfile.ZipFile(request["archive"]) as archive:
            self.assertEqual(archive.namelist(), ["build-manifest.json"])
            self.assertEqual(json.loads(archive.read("build-manifest.json"))["result"], "failure")

    def test_source_pins_flags_toolchains_scripts_and_images_invalidate_keys(self):
        baseline = self.prepare()["key"]
        target = self.component["targets"][TARGET]
        target["flags"]["configuration"] = "debug"
        self.write_manifest()
        self.assertNotEqual(self.prepare()["key"], baseline)
        target["flags"]["configuration"] = "release"
        target["toolchains"]["fixture"]["command"][-1] = "print('fixture-compiler-v2')"
        self.write_manifest()
        self.assertNotEqual(self.prepare()["key"], baseline)
        target["toolchains"]["fixture"]["command"][-1] = "print('fixture-compiler-v1')"
        self.write_manifest()
        with patch.dict(os.environ, {"ImageVersion": "different-image"}):
            self.assertNotEqual(self.prepare()["key"], baseline)
        original = self.recipe.read_text()
        self.recipe.write_text(original + "# A changed recipe\n", newline="\n")
        self.assertNotEqual(self.prepare()["key"], baseline)
        self.recipe.write_text(original, newline="\n")
        source = self.root / "3rdparty/example"
        (source / "library.txt").write_text("updated source\n", newline="\n")
        self.commit(source)
        revision = build.git(source, "rev-parse", "HEAD").decode().strip()
        subprocess.run(["git", "update-index", "--cacheinfo", "160000", revision, "3rdparty/example"], cwd=self.root, check=True)
        self.component["sources"]["3rdparty/example"]["revision"] = revision
        self.write_manifest()
        self.assertNotEqual(self.prepare()["key"], baseline)

    def test_platform_identity_invalidates_cache_even_for_identical_source_payloads(self):
        first = self.prepare()["key"]
        other = next(name for name in build.PLATFORMS if name != TARGET)
        self.component["targets"][other] = copy.deepcopy(self.component["targets"][TARGET])
        self.write_manifest()
        # Compare both keys against the same multi-target manifest.
        first = self.prepare()["key"]
        system, architectures, _ = build.PLATFORMS[other]
        with patch("build.platform.system", return_value=system), patch("build.platform.machine", return_value=next(iter(architectures))):
            second = build.prepare(self.root, "example", other)["key"]
        self.assertNotEqual(first, second)

    def test_build_uses_git_snapshot_not_ignored_source_worktree_bytes(self):
        baseline = self.prepare()["key"]
        (self.root / "3rdparty/example/ignored.txt").write_text("Not a build input", newline="\n")
        self.assertEqual(self.prepare()["key"], baseline)
        self.assertEqual(self.run_build()["result"], "success")

    def test_source_exports_ignore_ambient_git_line_ending_conversion(self):
        source = self.root / "3rdparty/example"
        baseline = self.prepare()["inputs"]["sources"]["3rdparty/example"]["archiveSha256"]
        subprocess.run(["git", "config", "core.autocrlf", "true"], cwd=source, check=True)
        self.assertEqual(self.prepare()["inputs"]["sources"]["3rdparty/example"]["archiveSha256"], baseline)
        self.run_build(cold=True)
        self.assertEqual((self.root / f"artifacts/dependencies/example/{TARGET}/payload/library.txt").read_bytes(), b"source payload\n")

    def update_source_pin(self):
        source = self.root / "3rdparty/example"
        self.commit(source)
        revision = build.git(source, "rev-parse", "HEAD").decode().strip()
        subprocess.run(["git", "update-index", "--cacheinfo", "160000", revision, "3rdparty/example"], cwd=self.root, check=True)
        self.component["sources"]["3rdparty/example"]["revision"] = revision
        self.write_manifest()
        return source

    def set_exports(self, paths):
        self.component["sources"]["3rdparty/example"]["exports"] = paths
        self.write_manifest()

    def test_selected_export_excludes_unselected_code_and_preserves_cold_cached_payload(self):
        source = self.root / "3rdparty/example"
        (source / "unselected.txt").write_text("Unselected upstream code\n", newline="\n")
        self.update_source_pin()
        self.set_exports(["LICENSE", "library.txt"])
        cold = self.run_build(cold=True)
        snapshot = self.root / f"artifacts/dependencies/example/{TARGET}/example.tar"
        with tarfile.open(snapshot) as archive:
            self.assertEqual({p.name for p in archive if p.isfile()}, {"LICENSE", "library.txt"})
            self.assertEqual(archive.pax_headers["comment"], self.component["sources"]["3rdparty/example"]["revision"])
        self.assertFalse((snapshot.parent / "sources/3rdparty/example/unselected.txt").exists())
        warm = self.run_build()
        self.assertTrue(warm["cacheHit"])
        self.assertEqual(cold["key"], warm["key"])
        self.assertEqual(cold["files"], warm["files"])
        self.assertEqual(warm["inputs"]["sources"]["3rdparty/example"]["exports"], ["LICENSE", "library.txt"])

    def test_export_selection_is_part_of_the_cache_key_even_if_payload_is_identical(self):
        whole = self.prepare()
        self.set_exports(["LICENSE", "library.txt"])
        selected = self.run_build(cold=True)
        self.assertNotEqual(whole["key"], selected["key"])
        self.assertNotIn("exports", whole["inputs"]["sources"]["3rdparty/example"])
        self.component["sources"]["3rdparty/example"].pop("exports")
        self.write_manifest()
        restored_whole = self.run_build()
        self.assertFalse(restored_whole["cacheHit"])
        self.assertEqual(selected["files"], restored_whole["files"])

    def test_missing_declared_license_cannot_be_excluded_from_export(self):
        self.set_exports(["library.txt"])
        with self.assertRaisesRegex(ValueError, "every declared license/notice"):
            self.prepare()

    def test_missing_export_path_fails_before_recipe_or_cache_use(self):
        self.set_exports(["LICENSE", "missing", "library.txt"])
        self.component["sources"]["3rdparty/example"]["exports"].sort()
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "missing or has no file content"):
            self.run_build()
        self.assertFalse((self.root / "artifacts/counter").exists())

    def test_export_paths_reject_empty_unsafe_unsorted_duplicate_overlapping_and_case_colliding_sets(self):
        for paths in (None, [], "LICENSE", ["LICENSE", "../src"], ["LICENSE", "src/*"], ["LICENSE", ":(exclude)src"],
                      ["library.txt", "LICENSE"], ["LICENSE", "LICENSE"], ["LICENSE", "src", "src/api"],
                      ["LICENSE", "SRC", "src"], ["LICENSE", "SRC", "src/api"], ["LICENSE", "src"] * 130):
            with self.subTest(paths=paths):
                self.set_exports(paths)
                with self.assertRaises(ValueError):
                    build.registry(self.root)

    def test_source_exports_remain_bounded_for_large_explicit_ui_inventories(self):
        self.set_exports(["LICENSE", *[f"source{index:04d}.kt" for index in range(4096)]])
        with self.assertRaisesRegex(ValueError, "bounded list"):
            build.registry(self.root)

    def test_large_literal_inventory_uses_bounded_git_commands_without_omissions(self):
        source = self.root / "3rdparty/example"
        paths = [f"source{index:04d}-" + "x" * 160 + "-映画.kt" for index in range(230)]
        for name in paths:
            (source / name).write_text(name + "\n", encoding="utf-8", newline="\n")
        self.update_source_pin()
        selected = sorted(["LICENSE", *paths])
        from unittest.mock import patch
        with patch.object(build, "git", wraps=build.git) as calls:
            entries = build.exported_entries(source, selected)
        self.assertEqual(set(entries), set(selected))
        self.assertGreater(calls.call_count, 1)
        for call in calls.call_args_list:
            arguments = call.args[1:]
            units = sum(len(str(value).encode("utf-16-le")) // 2 + 3 for value in arguments)
            self.assertLess(units, 9000)

    def test_literal_file_and_directory_exports_do_not_interpret_git_pathspec_magic(self):
        source = self.root / "3rdparty/example"
        for name in ("!literal", "selected", "selected-other", "unselected"):
            (source / name).mkdir()
            (source / name / "file.txt").write_text(name + "\n", newline="\n")
        self.update_source_pin()
        self.set_exports(["!literal", "LICENSE", "selected"])
        self.prepare()
        with tarfile.open(self.root / f"artifacts/dependencies/example/{TARGET}/example.tar") as archive:
            self.assertEqual({m.name for m in archive if m.isfile()}, {"!literal/file.txt", "LICENSE", "selected/file.txt"})

    def test_git_export_ignore_cannot_silently_omit_selected_sources_or_licenses(self):
        for ignored in ("library.txt", "LICENSE"):
            with self.subTest(ignored=ignored):
                source = self.root / "3rdparty/example"
                (source / ".gitattributes").write_text(ignored + " export-ignore\n", newline="\n")
                self.update_source_pin()
                self.set_exports(["LICENSE", "library.txt"])
                self.prepare()
                with tarfile.open(self.root / f"artifacts/dependencies/example/{TARGET}/example.tar") as archive:
                    self.assertEqual(archive.extractfile("LICENSE").read(), b"Fixture source license\n")
                    self.assertEqual(archive.extractfile("library.txt").read(), b"source payload\n")

    def test_git_export_substitution_cannot_change_pinned_source_bytes(self):
        source = self.root / "3rdparty/example"
        (source / "library.txt").write_text("$Format:%H$\n", newline="\n")
        (source / ".gitattributes").write_text("library.txt export-subst\n", newline="\n")
        self.update_source_pin()
        self.set_exports(["LICENSE", "library.txt"])
        self.prepare()
        with tarfile.open(self.root / f"artifacts/dependencies/example/{TARGET}/example.tar") as archive:
            self.assertEqual(archive.extractfile("library.txt").read(), b"$Format:%H$\n")

    def test_selected_tree_must_cover_all_nested_notices(self):
        source = self.root / "3rdparty/example"
        (source / "legal").mkdir()
        (source / "legal/NOTICE").write_text("Fixture nested attribution\n", newline="\n")
        self.update_source_pin()
        self.component["sources"]["3rdparty/example"]["notices"].append("legal/NOTICE")
        self.set_exports(["LICENSE", "legal", "library.txt"])
        result = self.run_build(cold=True)
        self.assertIn("payload/notices/example/legal/NOTICE", result["files"])

    def test_exports_do_not_bypass_review_of_excluded_nested_submodules(self):
        source = self.root / "3rdparty/example"
        (source / "client").mkdir()
        (source / "client/library.c").write_text("/* selected client */\n", newline="\n")
        (source / ".gitmodules").write_text('[submodule "nested"]\n path = omitted/nested\n url = https://example.invalid/nested.git\n', newline="\n")
        revision = self.component["sources"]["3rdparty/example"]["revision"]
        subprocess.run(["git", "update-index", "--add", "--cacheinfo", "160000", revision, "omitted/nested"], cwd=source, check=True)
        self.update_source_pin()
        self.set_exports(["LICENSE", "client", "library.txt"])
        self.component["sources"]["3rdparty/example"]["checkoutDirectories"] = ["client"]
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "Nested source is neither pinned separately nor explicitly unused"):
            self.prepare()
        self.component["sources"]["3rdparty/example"]["unusedSubmodules"] = {"omitted/nested": "Upstream tests, not used by this source profile"}
        self.write_manifest()
        self.assertEqual(self.run_build(cold=True)["result"], "success")

    def test_fresh_export_checkout_fetches_exact_pin_without_materializing_omitted_paths(self):
        source = self.root / "3rdparty/example"
        blob = build.git(source, "rev-parse", "HEAD:library.txt").decode().strip()
        # A valid Git name which cannot be materialized on Windows or POSIX filesystems.
        omitted = "omitted/" + "x" * 270 + ".txt"
        subprocess.run(["git", "update-index", "--add", "--cacheinfo", "100644", blob, omitted], cwd=source, check=True)
        subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "Unselected long path"], cwd=source, check=True)
        revision = build.git(source, "rev-parse", "HEAD").decode().strip()
        self.component["sources"]["3rdparty/example"]["revision"] = revision
        subprocess.run(["git", "update-index", "--cacheinfo", "160000", revision, "3rdparty/example"], cwd=self.root, check=True)
        # The remote default branch differs from the selected revision.
        subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "--allow-empty", "-qm", "Newer default branch"], cwd=source, check=True)
        remote = self.root / "remote.git"
        subprocess.run(["git", "clone", "--bare", str(source), str(remote)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.remove_source_fixture(source)
        self.set_exports(["LICENSE", "library.txt"])
        subprocess.run(["git", "submodule", "init", "--", "3rdparty/example"], cwd=self.root, check=True)
        build.initialize_export_source(self.root, "3rdparty/example", self.component["sources"]["3rdparty/example"], remote.as_uri())
        self.assertEqual(build.git(source, "rev-parse", "HEAD").decode().strip(), revision)
        self.assertEqual(build.git(source, "rev-list", "--count", "HEAD").strip(), b"1")
        self.assertEqual((source / "library.txt").read_text(), "source payload\n")
        self.assertFalse((source / "omitted").exists())
        self.assertTrue((source / ".git").is_file())
        self.assertFalse(build.git(source, "status", "--porcelain").strip())
        result = self.run_build(cold=True)
        self.assertEqual(result["result"], "success")

    def test_uninitialized_export_source_does_not_replace_unrelated_files(self):
        source = self.root / "3rdparty/example"
        self.remove_source_fixture(source)
        source.mkdir()
        (source / "owner.txt").write_text("preserve\n", newline="\n")
        self.set_exports(["LICENSE", "library.txt"])
        with self.assertRaisesRegex(ValueError, "must be empty"):
            self.prepare()
        self.assertEqual((source / "owner.txt").read_text(), "preserve\n")

    def test_selected_long_paths_remain_clean_after_git_checkout(self):
        source = self.root / "3rdparty/example"
        blob = build.git(source, "rev-parse", "HEAD:library.txt").decode().strip()
        # Legal individual components; the complete Windows path exceeds MAX_PATH.
        selected = "selected/" + "/".join(["long-source-component" * 2] * 6) + "/original.txt"
        self.assertGreater(len(str(source / selected)), 260)
        subprocess.run(["git", "update-index", "--add", "--cacheinfo", "100644", blob, selected], cwd=source, check=True)
        subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "Selected long source"], cwd=source, check=True)
        revision = build.git(source, "rev-parse", "HEAD").decode().strip()
        self.component["sources"]["3rdparty/example"]["revision"] = revision
        subprocess.run(["git", "update-index", "--cacheinfo", "160000", revision, "3rdparty/example"], cwd=self.root, check=True)
        remote = self.root / "long-source.git"
        subprocess.run(["git", "clone", "--bare", str(source), str(remote)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.remove_source_fixture(source)
        self.set_exports(["LICENSE", "library.txt", selected])
        subprocess.run(["git", "submodule", "init", "--", "3rdparty/example"], cwd=self.root, check=True)
        build.initialize_export_source(self.root, "3rdparty/example", self.component["sources"]["3rdparty/example"], remote.as_uri())
        self.assertFalse(build.git(source, "status", "--porcelain").strip())
        entries = build.exported_entries(source, ["LICENSE", "library.txt", selected])
        archive = self.root / "long-source.tar"
        build.export_original_blobs(source, archive, entries, revision)
        with tarfile.open(archive) as package:
            self.assertEqual(package.extractfile(selected).read(), b"source payload\n")

    def test_exported_tree_rejects_case_colliding_files_and_parent_directories(self):
        source = self.root / "3rdparty/example"
        blob = build.git(source, "rev-parse", "HEAD:library.txt").decode().strip()
        # Construct Git tree entries directly, including on case-insensitive hosts.
        for name in ("module/Case/a.txt", "module/case/b.txt"):
            subprocess.run(["git", "update-index", "--add", "--cacheinfo", "100644", blob, name], cwd=source, check=True)
        subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "Case-colliding upstream tree"], cwd=source, check=True)
        with self.assertRaisesRegex(ValueError, "collide across platforms"):
            build.exported_entries(source, ["module"])

    @unittest.skipIf(os.name == "nt", "Windows symlink creation is host-policy dependent")
    def test_selected_symlink_target_is_verified_and_not_followed_into_omitted_code(self):
        source = self.root / "3rdparty/example"
        (source / "unselected.txt").write_text("Omitted code\n", newline="\n")
        (source / "alias.txt").symlink_to("unselected.txt")
        self.update_source_pin()
        self.set_exports(["LICENSE", "alias.txt", "library.txt"])
        self.prepare()
        with tarfile.open(self.root / f"artifacts/dependencies/example/{TARGET}/example.tar") as archive:
            self.assertTrue(archive.getmember("alias.txt").issym())
            self.assertEqual(archive.getmember("alias.txt").linkname, "unselected.txt")
            self.assertNotIn("unselected.txt", archive.getnames())

    def test_dirty_source_checkout_is_rejected_before_checkout_changes(self):
        source = self.root / "3rdparty/example/library.txt"
        source.write_text("uncommitted work\n", newline="\n")
        with self.assertRaisesRegex(ValueError, "local changes"):
            self.prepare()
        self.assertEqual(source.read_text(), "uncommitted work\n")

    def test_registry_rejects_mismatched_pins_and_unregistered_transitive_sources(self):
        source = self.component["sources"]["3rdparty/example"]
        revision = source["revision"]
        source["revision"] = "a" * 40
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "Git index pin"):
            build.registry(self.root)
        source["revision"] = revision
        source["requires"] = ["3rdparty/missing"]
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "undeclared dependency"):
            build.registry(self.root)

    def test_unavailable_components_cannot_report_a_successful_build(self):
        self.manifest["components"]["pending"] = {"status": "unavailable", "reason": "Source closure pending", "plannedIn": "PR03"}
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "unavailable"):
            build.build(self.root, "pending", TARGET)
        self.assertFalse((self.root / "artifacts/dependencies/pending").exists())

    def test_offline_controls_cannot_be_overridden_by_a_recipe(self):
        self.component["targets"][TARGET]["environment"]["PIP_NO_INDEX"] = "0"
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "offline controls"):
            build.registry(self.root)

    def test_measured_media_sdk_paths_survive_without_ambient_flags_or_credentials(self):
        paths = {"SYNCWATCH_VLC_MSYS_ROOT": "/private/toolchain/msys64",
                 "SYNCWATCH_VLC_EXTRA_TOOLS_ROOT": "/private/toolchain/tools",
                 "SYNCWATCH_VLC_AUTOTOOLS_ROOT": "/private/toolchain/autotools"}
        with patch.dict(os.environ, {**paths, "CFLAGS": "unmeasured compiler override",
                                     "GITHUB_TOKEN": "credential-fixture"}):
            environment = build.build_environment({"environment": {}})
        self.assertEqual({name: environment[name] for name in paths}, paths)
        self.assertNotIn("CFLAGS", environment)
        self.assertNotIn("GITHUB_TOKEN", environment)
        self.assertEqual(environment["PIP_NO_INDEX"], "1")

    def test_hosted_build_cannot_identify_modified_inputs_as_a_clean_commit(self):
        self.recipe.write_text(self.recipe.read_text() + "# Uncommitted change\n", newline="\n")
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            with self.assertRaises(subprocess.CalledProcessError):
                self.prepare()

    @unittest.skipIf(os.name == "nt", "Windows symlink creation is host-policy dependent")
    def test_generated_directories_cannot_follow_workspace_symlinks(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (self.root / "artifacts").symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symbolic links"):
            self.prepare()

    def test_unverified_toolchain_is_a_failure_before_build_or_cache_reuse(self):
        self.component["targets"][TARGET]["toolchains"]["fixture"]["pattern"] = "^different-compiler$"
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "toolchain"):
            self.prepare()
        self.assertFalse((self.root / "artifacts/counter").exists())

    def test_failed_sdk_probe_retains_its_context_and_diagnostic(self):
        self.component["targets"][TARGET]["toolchains"]["fixture"]["command"] = [
            "{python}", "-c", "print('fixture SDK diagnostic'); raise SystemExit(7)"]
        self.write_manifest()
        error = io.StringIO()
        with contextlib.redirect_stderr(error), self.assertRaises(subprocess.CalledProcessError):
            self.prepare()
        self.assertIn("example/" + TARGET + "/fixture", error.getvalue())
        self.assertIn("fixture SDK diagnostic", error.getvalue())
        self.assertFalse((self.root / "artifacts/counter").exists())

    def test_failing_and_empty_builds_cannot_enter_the_cache(self):
        for recipe in ("raise RuntimeError('controlled failure')\n", "pass\n"):
            with self.subTest(recipe=recipe):
                self.recipe.write_text(recipe, newline="\n")
                request = self.prepare()
                with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                    self.run_build(cold=True)
                self.assertFalse(Path(request["cacheFile"]).exists())
                with zipfile.ZipFile(request["archive"]) as archive:
                    self.assertEqual(json.loads(archive.read("build-manifest.json"))["result"], "failure")

    def test_pr_and_main_local_cache_writes_have_different_scopes(self):
        with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/main"}):
            trusted = self.prepare()
        with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_RUN_ID": "123", "GITHUB_REF": "refs/pull/7/merge"}):
            proposed = self.prepare()
        self.assertEqual(trusted["key"], proposed["key"])
        self.assertIn("/main/", trusted["cacheFile"].replace("\\", "/"))
        self.assertIn("/untrusted-123/", proposed["cacheFile"].replace("\\", "/"))

    def test_cached_inputs_inventory_and_member_paths_are_checked_before_extraction(self):
        self.run_build()
        request = self.prepare()
        original = Path(request["cacheFile"]).read_bytes()
        for alteration in ("wrong-inputs", "traversal", "missing-member", "symlink", "case-collision"):
            with self.subTest(alteration=alteration):
                with zipfile.ZipFile(io.BytesIO(original)) as archive:
                    members = {name: archive.read(name) for name in archive.namelist()}
                manifest = json.loads(members["build-manifest.json"])
                if alteration == "wrong-inputs":
                    manifest["inputs"]["artifactSchema"] = 999
                elif alteration == "traversal":
                    members["../outside"] = b"unsafe"
                elif alteration == "missing-member":
                    del members["payload/library.txt"]
                elif alteration == "case-collision":
                    members["payload/Library.txt"] = b"ambiguous"
                members["build-manifest.json"] = json.dumps(manifest).encode()
                broken = self.root / "broken.zip"
                with zipfile.ZipFile(broken, "w") as archive:
                    for name, data in members.items():
                        if alteration == "symlink" and name == "payload/library.txt":
                            entry = zipfile.ZipInfo(name)
                            entry.external_attr = 0o120777 << 16
                            archive.writestr(entry, data)
                        else:
                            archive.writestr(name, data)
                destination = self.root / "extract"
                with self.assertRaises(ValueError):
                    build.unpack(broken, destination, request)
                self.assertFalse(destination.exists())


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.components = {
            "ready": {"status": "ready", "always": False, "watch": ["apps/host"], "inputs": ["tools/host.py"], "sources": {"3rdparty/host": {}}, "targets": {"windows-x64": {}}},
            "pending": {"status": "unavailable", "reason": "Unresolved source graph"},
        }

    def test_documentation_changes_do_not_select_unrelated_expensive_builds(self):
        matrix, unavailable = build.selected(self.components, ["docs/development.md"])
        self.assertEqual(matrix, {"include": []})
        self.assertEqual(unavailable, {"pending": "Unresolved source graph"})

    def test_source_configuration_toolchain_and_framework_changes_select_builds(self):
        for path in ("3rdparty/host", "tools/host.py", "apps/host/config.json", "tools/native/toolchains.json", ".github/workflows/repository.yml", ".gitmodules"):
            with self.subTest(path=path):
                matrix, _ = build.selected(self.components, [path])
                self.assertEqual(matrix["include"], [{"component": "ready", "platform": "windows-x64", "cold": False}])

    def test_cold_and_unknown_comparison_select_all_ready_components(self):
        for paths, cold in (([], True), (None, False)):
            matrix, unavailable = build.selected(self.components, paths, cold)
            self.assertEqual(len(matrix["include"]), 1)
            self.assertEqual(matrix["include"][0]["cold"], cold)
            self.assertIn("pending", unavailable)


if __name__ == "__main__":
    unittest.main()
