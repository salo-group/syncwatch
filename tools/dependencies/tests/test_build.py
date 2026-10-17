import contextlib
import copy
import io
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build


REPOSITORY = Path(__file__).resolve().parents[3]
TARGET = next(name for name, (system, architectures, _) in build.PLATFORMS.items() if platform.system() == system and platform.machine() in architectures)


class DependencyBuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
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
        (source / "LICENSE").write_text("Fixture source license\n")
        (source / "library.txt").write_text("source payload\n")
        (source / ".gitignore").write_text("ignored.txt\n")
        self.commit(source)
        revision = build.git(source, "rev-parse", "HEAD").decode().strip()
        (self.root / ".gitmodules").write_text('[submodule "example"]\n path = 3rdparty/example\n url = https://example.invalid/source.git\n')
        subprocess.run(["git", "update-index", "--add", "--cacheinfo", "160000", revision, "3rdparty/example"], cwd=self.root, check=True)
        (self.root / ".gitignore").write_text("artifacts/\n.local/\n__pycache__/\n")
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
        )
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
        subprocess.run(["git", "add", "."], cwd=root, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "Fixture"], cwd=root, check=True)

    def write_manifest(self):
        (self.root / "tools/dependencies/components.json").write_text(json.dumps(self.manifest, indent=2) + "\n")

    def prepare(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return build.prepare(self.root, "example", TARGET)

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
        self.recipe.write_text(original + "# A changed recipe\n")
        self.assertNotEqual(self.prepare()["key"], baseline)
        self.recipe.write_text(original)
        source = self.root / "3rdparty/example"
        (source / "library.txt").write_text("updated source\n")
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
        (self.root / "3rdparty/example/ignored.txt").write_text("Not a build input")
        self.assertEqual(self.prepare()["key"], baseline)
        self.assertEqual(self.run_build()["result"], "success")

    def test_source_exports_ignore_ambient_git_line_ending_conversion(self):
        source = self.root / "3rdparty/example"
        baseline = self.prepare()["inputs"]["sources"]["3rdparty/example"]["archiveSha256"]
        subprocess.run(["git", "config", "core.autocrlf", "true"], cwd=source, check=True)
        self.assertEqual(self.prepare()["inputs"]["sources"]["3rdparty/example"]["archiveSha256"], baseline)
        self.run_build(cold=True)
        self.assertEqual((self.root / f"artifacts/dependencies/example/{TARGET}/payload/library.txt").read_bytes(), b"source payload\n")

    def test_dirty_source_checkout_is_rejected_before_checkout_changes(self):
        source = self.root / "3rdparty/example/library.txt"
        source.write_text("uncommitted work\n")
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

    def test_hosted_build_cannot_identify_modified_inputs_as_a_clean_commit(self):
        self.recipe.write_text(self.recipe.read_text() + "# Uncommitted change\n")
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

    def test_failing_and_empty_builds_cannot_enter_the_cache(self):
        for recipe in ("raise RuntimeError('controlled failure')\n", "pass\n"):
            with self.subTest(recipe=recipe):
                self.recipe.write_text(recipe)
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
