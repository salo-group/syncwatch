"""Check the headless selection independently of the native build."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

tools = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(tools / name) for name in ("syncplay-runtime", "python-build", "dependencies")]
import engine_sources


class EngineSelectionTests(unittest.TestCase):
    def test_stock_players_are_source_only(self):
        source = {
            "syncplay/client.py": b"original client",
            "syncplay/server.py": b"original server",
            "syncplay/players/__init__.py": b"original player package",
            "syncplay/players/basePlayer.py": b"original base player",
            "syncplay/ui/ConfigurationGetter.py": b"class Configuration:\n"
                b"    def __init__(self):\n        self._config = {}\n",
        }
        with tempfile.TemporaryDirectory() as directory, patch.object(engine_sources, "configure"):
            files, changes = engine_sources.assemble({"syncplay": source}, Path(directory))

        self.assertFalse(any(name.startswith("syncplay/players/") for name in files))
        self.assertFalse(any("/players/" in name for name in changes))
        self.assertEqual(files["syncplay/server.py"], source["syncplay/server.py"])


if __name__ == "__main__":
    unittest.main()
