"""Bound decoding of the authored media used by native output checks."""

import base64
import hashlib
import json
import lzma
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fixtures


class EmbeddedFixtureChecks(unittest.TestCase):
    def test_tracked_authored_fixture_decodes_to_the_recorded_matroska(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = fixtures.embedded_tracks(Path(temporary))
            self.assertEqual(path.read_bytes()[:4], bytes.fromhex("1a45dfa3"))

    def test_sparse_fixture_is_a_separate_bounded_authored_matroska(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = fixtures.sparse_video(Path(temporary))
            self.assertEqual(path.name, "owned-sparse-video.mkv")
            self.assertEqual(path.read_bytes()[:4], bytes.fromhex("1a45dfa3"))
            self.assertFalse((Path(temporary) / "owned-embedded-tracks.mkv").exists())

    def test_corrupt_truncated_and_trailing_streams_fail_without_publishing_media(self):
        content = b"authored bytes"
        compressed = lzma.compress(content)
        for stream in [compressed[:-3], compressed + b"trailing", lzma.compress(b"changed bytes")]:
            with self.subTest(stream=stream), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); media = root / "media"; media.mkdir()
                (media / "input.b64").write_bytes(base64.encodebytes(stream))
                (media / "embedded-tracks.json").write_text(json.dumps({"schemaVersion": 1,
                    "encoding": "base64+xz", "encoded": "input.b64", "bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest()}))
                with patch.object(fixtures, "__file__", str(root / "fixtures.py")), self.assertRaises(ValueError):
                    fixtures.embedded_tracks(root)
                self.assertFalse((root / "owned-embedded-tracks.mkv").exists())


if __name__ == "__main__":
    unittest.main()
