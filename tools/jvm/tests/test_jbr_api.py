import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jbr_api


class JbrApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.output = self.root / "payload"
        header = b"/* Fixture copyright. Licensed under the Apache License, Version 2.0 */\n"
        self.files = {"src/fixture/Service.java": header + b"package fixture;\n",
                      "tools/javac/SourceGenerator.java": header + b"class SourceGenerator {}\n",
                      "LICENSE": b"Fixture license\n", "README.md": b"Fixture upstream README\n"}
        for name, data in self.files.items():
            p = self.source / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        generator = "tools/javac/SourceGenerator.java"
        generated = b"/* Original generator fixture output. */\n"
        (self.root / "tools/jvm").mkdir(parents=True)
        self.profile = {"schemaVersion": 1, "coordinate": "org.jetbrains.runtime:jbr-api:1.9.0", "sourcePath": "3rdparty/jbr-api",
                        "revision": "f" * 40, "license": "Apache-2.0", "javaRelease": 8, "moduleRelease": 9,
                        "sources": {"src/fixture/Service.java": jbr_api.digest(self.files["src/fixture/Service.java"])},
                        "buildSources": {generator: jbr_api.digest(self.files[generator])},
                        "provenance": {n: jbr_api.digest(self.files[n]) for n in ("LICENSE", "README.md")},
                        "generatedSources": {jbr_api.GENERATED: jbr_api.digest(generated)}}
        context = patch.object(jbr_api, "ROOT", self.root)
        context.start()
        self.addCleanup(context.stop)
        common = jbr_api.common_entries(self.files, self.profile, "fixture")
        products = {jbr_api.BINARY: jbr_api.jar_bytes({"fixture/Service.class": b"Fixture bytecode"} | common),
                    jbr_api.SOURCES: jbr_api.jar_bytes(self.files | {jbr_api.GENERATED: generated} | common)}
        self.profile["expectedJars"] = {n: jbr_api.digest(d) for n, d in products.items()}
        for name, data in (products | jbr_api.support_files(self.files, self.profile, "fixture")).items():
            p = self.output / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)

    def verify(self):
        jbr_api.verify(self.output, self.files, self.profile, "fixture")

    def rewrite_jar(self, name, change):
        path = self.output / name
        with zipfile.ZipFile(path) as archive:
            entries = {n: archive.read(n) for n in archive.namelist()}
        change(entries)
        path.write_bytes(jbr_api.jar_bytes(entries))
        self.profile["expectedJars"][name] = jbr_api.digest(path.read_bytes())

    def test_foreign_hosts_reject_compilation_and_probes_before_sdk_access(self):
        for target in ("macos-arm64", "windows-x64"):
            with self.subTest(target=target), patch.object(jbr_api.jdk, "current_target", return_value=target):
                for operation in (lambda: jbr_api.compile_sources(None, None, None),
                                  lambda: jbr_api.probe(None, None), jbr_api.main):
                    with self.assertRaisesRegex(ValueError, "only on Linux"):
                        operation()
        with patch.object(jbr_api.jdk, "current_target", return_value="linux-x64"):
            self.assertIsNone(jbr_api.require_linux())
            for target in ("macos-arm64", "windows-x64"):
                with patch.object(sys, "argv", ["jbr_api.py", "verify", "--platform", target]), patch.object(sys, "stderr", io.StringIO()):
                    with self.assertRaises(SystemExit) as failure:
                        jbr_api.main()
                    self.assertEqual(failure.exception.code, 2)

    def test_complete_original_generated_and_license_inventories_pass(self):
        self.assertEqual(jbr_api.source_files(self.source, self.profile), self.files)
        self.verify()

    def test_changed_source_or_new_source_or_notice_requires_review(self):
        for name, data in (("src/fixture/Service.java", b"changed"), ("tools/Unexpected.java", b"unreviewed"), ("NOTICE", b"new attribution")):
            with self.subTest(name=name):
                p = self.source / name
                p.parent.mkdir(parents=True, exist_ok=True)
                original = p.read_bytes() if p.exists() else None
                p.write_bytes(data)
                with self.assertRaises(ValueError):
                    jbr_api.source_files(self.source, self.profile)
                if original is None:
                    p.unlink()
                else:
                    p.write_bytes(original)

    def test_removed_original_license_header_cannot_be_hidden_by_rehashing(self):
        name = next(iter(self.profile["sources"]))
        (self.source / name).write_bytes(b"package fixture;\n")
        self.profile["sources"][name] = jbr_api.digest(b"package fixture;\n")
        with self.assertRaisesRegex(ValueError, "original Apache header"):
            jbr_api.source_files(self.source, self.profile)

    def test_unreviewed_source_member_is_rejected_after_outer_digest_rewrite(self):
        self.rewrite_jar(jbr_api.SOURCES, lambda entries: entries.update({
            "META-INF/modified-build-sources/tools/javac/SourceGenerator.java": b"Unreviewed source"}))
        with self.assertRaisesRegex(ValueError, "source JAR inventory"):
            self.verify()

    def test_corrupted_payload_or_binary_fails_before_cache_success(self):
        (self.output / jbr_api.BINARY).write_bytes(b"corrupted cache")
        with self.assertRaisesRegex(ValueError, "reviewed source/compiler output"):
            self.verify()
        (self.output / "unexpected-runtime.dll").write_bytes(b"unexpected runtime")
        with self.assertRaisesRegex(ValueError, "missing or unexpected"):
            self.verify()

    def test_embedded_license_remains_required_after_outer_digest_rewrite(self):
        self.rewrite_jar(jbr_api.BINARY, lambda entries: entries.pop("META-INF/LICENSE"))
        with self.assertRaises((ValueError, KeyError)):
            self.verify()

    def test_original_and_generated_source_corruption_is_rejected(self):
        original = (self.output / jbr_api.SOURCES).read_bytes()
        for name in ("src/fixture/Service.java", "tools/javac/SourceGenerator.java", jbr_api.GENERATED):
            with self.subTest(name=name):
                (self.output / jbr_api.SOURCES).write_bytes(original)
                self.rewrite_jar(jbr_api.SOURCES, lambda entries: entries.update({name: b"changed source"}))
                with self.assertRaisesRegex(ValueError, "source"):
                    self.verify()

    def test_missing_payload_notice_is_rejected(self):
        (self.output / "THIRD-PARTY-NOTICES.txt").unlink()
        with self.assertRaisesRegex(ValueError, "missing or unexpected"):
            self.verify()

    def test_archive_requires_exact_source_commit(self):
        for revision in (self.profile["revision"], "e" * 40, None):
            with self.subTest(revision=revision):
                path = self.root / "source.tar"
                with tarfile.open(path, "w", format=tarfile.PAX_FORMAT, pax_headers={"comment": revision} if revision else {}) as archive:
                    for name, data in self.files.items():
                        member = tarfile.TarInfo(name)
                        member.size = len(data)
                        archive.addfile(member, io.BytesIO(data))
                if revision == self.profile["revision"]:
                    self.assertEqual(jbr_api.source_files(path, self.profile), self.files)
                else:
                    with self.assertRaisesRegex(ValueError, "different Git revision"):
                        jbr_api.source_files(path, self.profile)

    def test_profile_revision_license_and_external_graph_match_registration(self):
        (self.root / "tools/jvm/jbr_api.json").write_text(json.dumps(self.profile))
        (self.root / "tools/jvm/toolchains.json").write_text(json.dumps({"version": "fixture"}))
        registry = self.root / "tools/dependencies/components.json"
        registry.parent.mkdir(parents=True)
        source = {"revision": self.profile["revision"], "license": self.profile["license"], "requires": []}
        for change in ({}, {"revision": "e" * 40}, {"license": "Different-License"}, {"requires": ["3rdparty/undeclared"]}):
            with self.subTest(change=change):
                registry.write_text(json.dumps({"components": {"jbr-api": {"sources": {self.profile["sourcePath"]: source | change}}}}))
                if change:
                    with self.assertRaisesRegex(ValueError, "differs from the registered graph"):
                        jbr_api.profile_data()
                else:
                    self.assertEqual(jbr_api.profile_data(), (self.profile, {"version": "fixture"}))


if __name__ == "__main__":
    unittest.main()
