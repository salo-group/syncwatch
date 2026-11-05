"""Reject clock-only or incomplete native decoder reset receipts."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from package_report import validate_seek_reset, validate_subtitle_seek


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
    def test_complete_native_corpus_is_required(self):
        valid = {name: {"seeks": 7, "restored": 5 if name in {"normal", "duplicate"} else 0,
                       "ownedLibraryLoaded": True, "physicalOutputAccepted": False}
                 for name in ["normal", "expired-duration", "wrong-track", "bad-offset", "bad-cluster", "duplicate"]}
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
