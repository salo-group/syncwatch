from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rust_graph


class RustWorkspaceTests(unittest.TestCase):
    graph = {"crates": [{"name": "openssl", "version": "0.10.81", "source": "rust-openssl", "path": "openssl"}],
             "sources": {"cryptography": {"path": "3rdparty/python-cryptography"},
                         "rust-openssl": {"path": "3rdparty/rust-openssl"}}}

    def test_configuration_writes_only_the_owned_workspace(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            cryptography = work / "originals/python-cryptography"
            crate = work / "originals/rust-openssl"
            for path in (cryptography, crate):
                path.mkdir(parents=True)
                (path / "Cargo.toml").write_bytes(b"[workspace]\nmembers = []\n")
            original_lock = cryptography / "Cargo.lock"
            original_lock.write_bytes(b"version = 4\n")
            with patch.object(rust_graph, "graph_data", return_value=self.graph):
                root = rust_graph.configure(work)
            self.assertEqual(root.parent, work)
            self.assertIn("../originals/python-cryptography/src/rust", (root / "Cargo.toml").read_text())
            settings = (root / ".cargo/config.toml").read_text()
            self.assertIn('path = "../originals/rust-openssl/openssl"', settings)
            self.assertNotIn(str(work), settings)
            self.assertEqual(original_lock.read_bytes(), b"version = 4\n")
            for path in (cryptography, crate):
                self.assertEqual((path / "Cargo.toml").read_bytes(), b"[workspace]\nmembers = []\n")

    def test_restored_lock_and_workspace_must_match_the_pinned_inputs(self):
        source = {"Cargo.toml": b'[workspace]\nmembers = ["src/rust/"]\n[workspace.package]\nversion = "0.50.2"\n',
                  "src/rust/Cargo.toml": b'[package]\nname = "cryptography-rust"\nversion.workspace = true\n'}
        authority = {"crateAuthorities": {"openssl": {"source": "rust-openssl", "version": "0.10.81"}}}
        packages = [
            (rust_graph.WORKSPACE, rust_graph.VERSION),
            ("cryptography-rust", "0.50.2"),
            ("openssl", "0.10.81"),
        ]
        lock = 'version = 4\n' + ''.join(
            f'\n[[package]]\nname = "{name}"\nversion = "{version}"\n' for name, version in packages)
        with tempfile.TemporaryDirectory() as temporary, patch.object(rust_graph, "graph_data", return_value=self.graph):
            payload = Path(temporary)
            root = payload / rust_graph.CONFIGURATION
            for name, data in rust_graph.build_recipe().items():
                file = root / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(data)
            lockfile = root / "Cargo.lock"
            lockfile.write_text(lock, encoding="utf-8")
            rust_graph.verify_configuration(payload, source, authority)
            lockfile.write_text(lock.replace("0.10.81", "0.10.80"), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "crate identities"):
                rust_graph.verify_configuration(payload, source, authority)
            lockfile.write_text(lock, encoding="utf-8")
            (root / "src/lib.rs").unlink()
            with self.assertRaisesRegex(ValueError, "pinned build recipe"):
                rust_graph.verify_configuration(payload, source, authority)
