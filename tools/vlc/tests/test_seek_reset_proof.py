"""Reject clock-only or incomplete native decoder reset receipts."""

from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from package_report import validate_seek_reset, validate_subtitle_seek
from native_probe import verify_subtitle_seek


class SeekResetProofChecks(unittest.TestCase):
    def setUp(self):
        self.valid = {"success": True, "pausedSeeks": 30, "decodedBefore": 70,
                      "decodedAfter": 80, "actualClock": True, "normalClose": True,
                      "ownedLibraryLoaded": True, "temporaryFixturesRemoved": True,
                      "physicalOutputAccepted": False}

    def test_fresh_decoder_progress_and_normal_owned_close_are_accepted(self):
        validate_seek_reset(self.valid)

    def test_clock_progress_without_fresh_decoded_video_is_rejected(self):
        for after in [70, 73, True, None]:
            with self.subTest(after=after), self.assertRaises(ValueError):
                validate_seek_reset(self.valid | {"decodedAfter": after})

    def test_missing_or_failed_required_receipts_are_rejected(self):
        for key in self.valid:
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_seek_reset({k: v for k, v in self.valid.items() if k != key})
        for change in [{"normalClose": False}, {"actualClock": False}, {"success": False},
                       {"ownedLibraryLoaded": False}, {"temporaryFixturesRemoved": False},
                       {"physicalOutputAccepted": True}, {"pausedSeeks": 3}, {"decodedBefore": -1}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_seek_reset(self.valid | change)
        with self.assertRaises(ValueError):
            validate_seek_reset(None)


class SubtitleSeekProofChecks(unittest.TestCase):
    def test_failed_probe_setup_removes_partial_outputs(self):
        for stage in ["compile", "fixture"]:
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                executable = directory / "subtitle-seek-probe"
                fixture = directory / "owned-embedded-tracks.mkv"

                def compile_probe(*_):
                    executable.write_bytes(b"owned partial probe")
                    if stage == "compile":
                        raise OSError("compiler interrupted")
                    return executable, directory / "libvlc"

                def write_fixture(*_):
                    fixture.write_bytes(b"owned partial fixture")
                    raise ValueError("fixture validation failed")

                with patch("native_probe.compile_consumer", side_effect=compile_probe), \
                        patch("native_probe.embedded_tracks", side_effect=write_fixture):
                    with self.assertRaises((OSError, ValueError)):
                        verify_subtitle_seek(SimpleNamespace(target="macos-arm64"), directory, directory)
                self.assertFalse(executable.exists(), "compiled or partial probe leaked")
                self.assertFalse(fixture.exists(), "partial fixture leaked")

    def test_complete_native_corpus_is_required(self):
        valid = {name: {"seeks": 7, "restored": 5 if name in {"normal", "duplicate"} else 0,
                       "ownedLibraryLoaded": True, "physicalOutputAccepted": False}
                 for name in ["normal", "expired-duration", "wrong-track", "bad-offset", "bad-cluster", "duplicate"]}
        valid.update({name: {"seeks": 3, "restored": 0 if name == "bounded-oversized" else 3,
                             "ownedLibraryLoaded": True, "physicalOutputAccepted": False}
                      for name in ["bounded-small", "bounded-oversized", "bounded-aggregate"]})
        validate_subtitle_seek(valid)
        for name in valid:
            with self.subTest(missing=name), self.assertRaises(ValueError):
                validate_subtitle_seek({key: value for key, value in valid.items() if key != name})
            with self.subTest(failed=name), self.assertRaises(ValueError):
                validate_subtitle_seek(valid | {name: valid[name] | {"seeks": 0}})
        with self.assertRaises(ValueError):
            validate_subtitle_seek(None)


if __name__ == "__main__":
    unittest.main()
