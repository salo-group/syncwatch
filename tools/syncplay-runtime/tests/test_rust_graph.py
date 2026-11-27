from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rust_graph


class RustWorkspaceTests(unittest.TestCase):
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
            sources = {"cryptography": cryptography, "rust-openssl": crate}
            graph = {"crates": [{"name": "openssl", "source": "rust-openssl", "path": "openssl"}]}
            with patch.object(rust_graph, "graph_data", return_value=graph):
                root = rust_graph.configure(work, sources)
            self.assertEqual(root.parent, work)
            self.assertIn("../originals/python-cryptography/src/rust", (root / "Cargo.toml").read_text())
            settings = (root / ".cargo/config.toml").read_text()
            self.assertIn('path = "../originals/rust-openssl/openssl"', settings)
            self.assertNotIn(str(work), settings)
            self.assertEqual(original_lock.read_bytes(), b"version = 4\n")
            for path in (cryptography, crate):
                self.assertEqual((path / "Cargo.toml").read_bytes(), b"[workspace]\nmembers = []\n")
