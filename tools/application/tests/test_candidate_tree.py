import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("candidate_tree", Path(__file__).resolve().parents[1] / "candidate_tree.py")
TREE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = TREE
SPEC.loader.exec_module(TREE)
TARGET = {"darwin": "macos-arm64", "win32": "windows-x64", "linux": "linux-x64"}[sys.platform]


class CandidateTreeTests(unittest.TestCase):
    def fixture(self, root):
        source = root / "SDK Кино 映画"
        (source / "nested").mkdir(parents=True)
        (source / "nested/movie.txt").write_bytes(b"immutable component")
        (source / "launcher").write_bytes(b"launcher")
        if os.name != "nt":
            (source / "launcher").chmod(0o755)
        return source

    def assemble(self, destination, components):
        return TREE.assemble(destination, components, platform=TARGET, source_commit="a" * 40)

    def test_relocated_candidate_retains_exact_bytes_modes_and_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = self.fixture(root)
            before = TREE.inventory(source)
            output = root / "candidate"
            receipt = self.assemble(output, [TREE.Component(source, "SyncWatch/Media")])
            self.assertEqual(TREE.inventory(source), before)
            self.assertEqual(receipt["files"], TREE.inventory(output / "contents"))
            self.assertEqual(receipt, json.loads((output / "candidate.json").read_text()))
            self.assertFalse(receipt["releaseReady"])
            self.assertNotIn(str(source), json.dumps(receipt))
            relocated = root / "Пакет 映画 with spaces"
            output.rename(relocated)
            source.rename(root / "unavailable-original")
            self.assertEqual(TREE.inventory(relocated / "contents/SyncWatch/Media"), before)
            self.assertFalse(any(path.name.startswith(".stage-") for path in relocated.iterdir()))

    @unittest.skipUnless(os.name == "nt", "Published NTFS access is checked on Windows")
    def test_windows_publication_inherits_destination_reader_access(self):
        sid = subprocess.check_output(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value"], text=True, timeout=10).strip()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            # Model a destination readable by the same account's ordinary token,
            # even when the packaging process has an elevated administrator token.
            subprocess.run(["icacls", str(root), "/grant", f"*{sid}:(OI)(CI)(RX)", "/Q"],
                           check=True, capture_output=True, timeout=10)
            source = self.fixture(root)
            output = root / "candidate"
            self.assemble(output, [TREE.Component(source, "SyncWatch/Media")])
            script = r"""$ErrorActionPreference='Stop'
$wanted=[System.Security.AccessControl.FileSystemRights]::ReadAndExecute
$paths=@(Get-Item -LiteralPath $env:SYNCWATCH_ACCESS_ROOT)+@(Get-ChildItem -LiteralPath $env:SYNCWATCH_ACCESS_ROOT -Recurse -Force)
foreach($path in $paths){
 $rules=(Get-Acl -LiteralPath $path.FullName).GetAccessRules($true,$true,[System.Security.Principal.SecurityIdentifier])
 $allowed=@($rules | Where-Object { $_.IdentityReference.Value -eq $env:SYNCWATCH_ACCESS_SID -and $_.AccessControlType -eq 'Allow' -and ($_.FileSystemRights -band $wanted) -eq $wanted })
 if($allowed.Count -eq 0){throw ('Published path lost destination reader access: '+$path.FullName)}
}
"""
            result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                                    env=os.environ | {"SYNCWATCH_ACCESS_ROOT": str(output / "contents"),
                                                      "SYNCWATCH_ACCESS_SID": sid}, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_existing_output_and_user_data_are_never_adopted_or_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = self.fixture(root); output = root / "candidate"
            output.mkdir(); (output / "user.txt").write_bytes(b"preserve")
            with self.assertRaises(FileExistsError):
                self.assemble(output, [TREE.Component(source, "Media")])
            self.assertEqual((output / "user.txt").read_bytes(), b"preserve")

    def test_escape_overlap_and_ambiguous_destinations_fail_before_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = self.fixture(root)
            for value in ("", ".", "../Media", "/Media", "Media//x", "Media/.", "Media\\x", "C:Media"):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    self.assemble(root / "candidate", [TREE.Component(source, value)])
            for paths in (("Media", "Media"), ("Media", "media"), ("Media", "Media/nested"), ("Media/nested", "Media")):
                with self.subTest(paths=paths), self.assertRaises(ValueError):
                    self.assemble(root / "candidate", [TREE.Component(source, name) for name in paths])
            with self.assertRaisesRegex(ValueError, "overlap"):
                self.assemble(source / "candidate", [TREE.Component(source, "Media")])
            self.assertFalse((root / "candidate").exists())

    def test_nested_runtime_components_are_ordered_without_overwriting_application_assets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); media = self.fixture(root)
            application = root / "application"; (application / "bin").mkdir(parents=True)
            (application / "bin/SyncWatch").write_bytes(b"native application")
            output = root / "candidate"
            self.assemble(output, [TREE.Component(media, "SyncWatch/Media"), TREE.Component(application, "SyncWatch")])
            self.assertEqual((output / "contents/SyncWatch/bin/SyncWatch").read_bytes(), b"native application")
            self.assertEqual(TREE.inventory(output / "contents/SyncWatch/Media"), TREE.inventory(media))

    def test_partial_copy_failure_removes_only_the_new_owned_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = self.fixture(root); before = TREE.inventory(source)
            original = shutil.copytree
            def fail(*args, **kwargs):
                original(*args, **kwargs)
                raise OSError("authored copy failure")
            with patch.object(TREE.shutil, "copytree", side_effect=fail), self.assertRaisesRegex(OSError, "authored"):
                self.assemble(root / "candidate", [TREE.Component(source, "Media")])
            self.assertFalse((root / "candidate").exists())
            self.assertEqual(TREE.inventory(source), before)

    def test_source_change_during_copy_cannot_publish_a_candidate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = self.fixture(root); original = shutil.copytree
            def mutate(*args, **kwargs):
                result = original(*args, **kwargs)
                (source / "nested/movie.txt").write_bytes(b"changed by test")
                return result
            with patch.object(TREE.shutil, "copytree", side_effect=mutate), self.assertRaisesRegex(ValueError, "changed"):
                self.assemble(root / "candidate", [TREE.Component(source, "Media")])
            self.assertFalse((root / "candidate").exists())
            self.assertEqual((source / "nested/movie.txt").read_bytes(), b"changed by test")

    @unittest.skipIf(os.name == "nt", "Windows application inputs do not use symbolic links")
    def test_contained_links_relocate_and_escaping_links_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = self.fixture(root)
            (source / "alias").symlink_to("nested/movie.txt")
            (source / "directory-alias").symlink_to("nested", target_is_directory=True)
            self.assemble(root / "candidate", [TREE.Component(source, "Media")])
            source.rename(root / "unavailable-original")
            self.assertEqual((root / "candidate/contents/Media/alias").read_bytes(), b"immutable component")
            self.assertEqual((root / "candidate/contents/Media/directory-alias/movie.txt").read_bytes(), b"immutable component")
            source = root / "unavailable-original"
            (root / "outside").write_bytes(b"owned outside fixture")
            (source / "escape").symlink_to("../outside")
            with self.assertRaisesRegex(ValueError, "leaves its component"):
                self.assemble(root / "rejected", [TREE.Component(source, "Media")])
            self.assertFalse((root / "rejected").exists())

    @unittest.skipUnless(os.name == "nt", "NTFS junctions are checked on Windows")
    def test_windows_junction_cannot_escape_a_component(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = self.fixture(root); outside = root / "outside"
            outside.mkdir(); (outside / "keep.txt").write_bytes(b"owned outside fixture")
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(source / "junction"), str(outside)],
                                    capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            with self.assertRaisesRegex(ValueError, "reparse"):
                self.assemble(root / "rejected", [TREE.Component(source, "Media")])
            self.assertFalse((root / "rejected").exists())
            self.assertEqual((outside / "keep.txt").read_bytes(), b"owned outside fixture")
            with self.assertRaisesRegex(ValueError, "root cannot be a link"):
                self.assemble(root / "rejected-root", [TREE.Component(source / "junction", "Media")])
            self.assertFalse((root / "rejected-root").exists())


if __name__ == "__main__":
    unittest.main()
