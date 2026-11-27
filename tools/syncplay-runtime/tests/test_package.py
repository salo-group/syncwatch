import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import python_core
import engine_package as package
from source_files import encoded


class PackageAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.sdk = {'native': {'target': 'macos-arm64'}}
        self.sources = {'original-revision': 'fixture'}
        self.originals = {'notices/LICENSE': b'Complete original fixture grant\n'}
        package.write_files(self.root, self.originals)
        self.report = {'schemaVersion': 1, 'sdk': self.sdk, 'sources': self.sources,
                       'sourceBuiltHeadlessEngine': True, 'applicationProviderReady': False,
                       'compilerStandardSupportSdkBoundary': True,
                       'files': package.inventory(self.root)}
        self.receipt()

    def receipt(self):
        package.write_files(self.root, {'build-report.json': encoded(self.report)})

    def verify(self):
        return package.verify(self.root, self.sdk, [], self.sources, self.originals, ())

    def test_untrusted_payload_mutations_fail_before_native_execution(self):
        with patch.object(package, 'relocated_probe') as execute:
            for mutation in ['missing', 'changed', 'extra', 'alias']:
                with self.subTest(mutation=mutation):
                    path = self.root / 'notices/LICENSE'
                    path.write_bytes(self.originals['notices/LICENSE'])
                    if mutation == 'missing':
                        path.unlink()
                    elif mutation == 'changed':
                        path.write_bytes(b'short replacement')
                    elif mutation == 'extra':
                        (self.root / 'ambient-runtime.dll').write_bytes(b'foreign')
                    else:
                        path.unlink()
                        try:
                            path.symlink_to(self.root / 'build-report.json')
                        except OSError:
                            continue  # Windows unprivileged SDK may deny links.
                    with self.assertRaises(ValueError):
                        self.verify()
                    if path.is_symlink():
                        path.unlink()
                    (self.root / 'ambient-runtime.dll').unlink(missing_ok=True)
            execute.assert_not_called()

    def test_receipt_rehash_cannot_replace_complete_original_grant(self):
        (self.root / 'notices/LICENSE').write_bytes(b'SPDX-License-Identifier: MIT\n')
        found = package.inventory(self.root)
        found.pop('build-report.json')
        self.report['files'] = found
        self.receipt()
        with patch.object(package, 'relocated_probe') as execute:
            with self.assertRaisesRegex(ValueError, 'complete grant'):
                self.verify()
            execute.assert_not_called()

    def test_changed_source_sdk_and_provider_claims_are_rejected(self):
        for key, replacement in [('sdk', {}), ('sources', {}), ('applicationProviderReady', True)]:
            with self.subTest(field=key), patch.object(package, 'relocated_probe') as execute:
                original = copy.deepcopy(self.report)
                self.report[key] = replacement
                self.receipt()
                with self.assertRaisesRegex(ValueError, 'identity'):
                    self.verify()
                execute.assert_not_called()
                self.report = original

    def test_windows_module_table_must_match_the_selected_profile(self):
        target = "windows-x64"
        self.sdk["native"]["target"] = target
        configuration = {"configuration/cpython/" + name: data for name, data in package.build_recipe(target).items()}
        configuration["configuration/cpython/config.c"] = b'    {"math", PyInit_math},\n'
        package.write_files(self.root, configuration)
        self.report["files"] = package.inventory(self.root)
        self.report["files"].pop("build-report.json")
        self.receipt()
        with patch.object(package, "audit") as audit, patch.object(package, "relocated_probe") as native:
            with self.assertRaisesRegex(ValueError, "built-in module table"):
                package.verify(self.root, self.sdk, [], self.sources, self.originals, ("math", "sys"))
            audit.assert_not_called()
            native.assert_not_called()


if __name__ == '__main__':
    unittest.main()
