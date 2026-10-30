import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("playback_sdk_check_test", ROOT / "tools/playback/sdk_check.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class PlaybackSdkCheckTests(unittest.TestCase):
    def proofs(self, output):
        for name, data in {
            "native-proof.json": {"abiBytes": 80, "actualClock": True, "repeatedRelease": True, "physicalOutputAccepted": False},
            "media-input-proof.json": {"versionBytes": 64, "noHandleGrowth": True},
            "player-input-proof.json": {"repeatedPausedSeekResume": 3, "loadedContentHash": True, "positionalReadsPreservePlayback": True, "physicalOutputAccepted": False},
            "external-input-proof.json": {"actualSelectedID": True, "externalAudioSelectedID": True, "repeatedRelease": True, "physicalOutputAccepted": False},
        }.items():
            (output / name).write_text(json.dumps(data))

    def test_existing_sdk_and_current_sources_are_separate_and_fixtures_are_released(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); package = root / "payload"; package.mkdir()
            marker = package / "sdk"; marker.write_bytes(b"original SDK bytes")
            output = root / "consumer"
            def check(actual_package, actual_output):
                self.assertEqual(actual_package, package.resolve())
                self.proofs(actual_output)
                (actual_output / "owned-i420-pcm.avi").write_bytes(b"fixture")
                (actual_output / "playback-probe").write_bytes(b"probe")
                (actual_output / "owned-input-test").mkdir()
            with patch.object(gate, "native_check", side_effect=check) as native:
                proof = gate.verify(package, output)
            native.assert_called_once()
            self.assertTrue(proof["success"])
            self.assertTrue(proof["sources"])
            self.assertFalse(proof["applicationIntegrationReady"])
            self.assertFalse(proof["physicalOutputAccepted"])
            self.assertEqual(marker.read_bytes(), b"original SDK bytes")
            self.assertFalse((output / "owned-i420-pcm.avi").exists())
            self.assertFalse((output / "playback-probe").exists())
            self.assertFalse((output / "owned-input-test").exists())

    def test_missing_resume_progress_evidence_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); package = root / "payload"; package.mkdir(); output = root / "consumer"
            def missing(_, directory):
                self.proofs(directory)
                path = directory / "player-input-proof.json"
                proof = json.loads(path.read_text()); del proof["repeatedPausedSeekResume"]
                path.write_text(json.dumps(proof))
            with patch.object(gate, "native_check", side_effect=missing):
                with self.assertRaisesRegex(ValueError, "incomplete"):
                    gate.verify(package, output)
            self.assertFalse(json.loads((output / "first-party-proof.json").read_text())["success"])

    def test_native_failure_remains_failed_and_releases_owned_fixture(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); package = root / "payload"; package.mkdir(); output = root / "consumer"
            def fail(_, directory):
                (directory / "invalid.avi").write_bytes(b"fixture")
                raise RuntimeError("actual native check failed")
            with patch.object(gate, "native_check", side_effect=fail):
                with self.assertRaisesRegex(RuntimeError, "actual native"):
                    gate.verify(package, output)
            self.assertFalse(json.loads((output / "first-party-proof.json").read_text())["success"])
            self.assertFalse((output / "invalid.avi").exists())

    def test_payload_output_and_incomplete_runtime_proof_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); package = root / "payload"; package.mkdir()
            with self.assertRaisesRegex(ValueError, "SDK payload"):
                gate.verify(package, package / "consumer")
            output = root / "consumer"
            def incomplete(_, directory):
                self.proofs(directory)
                path = directory / "player-input-proof.json"
                proof = json.loads(path.read_text()); proof["loadedContentHash"] = False
                path.write_text(json.dumps(proof))
            with patch.object(gate, "native_check", side_effect=incomplete):
                with self.assertRaisesRegex(ValueError, "incomplete"):
                    gate.verify(package, output)
            self.assertFalse(json.loads((output / "first-party-proof.json").read_text())["success"])

    def test_changed_first_party_sources_invalidate_the_fresh_proof(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); package = root / "payload"; package.mkdir()
            source = root / "input.c"; source.write_bytes(b"before")
            output = root / "consumer"
            def mutate(_, directory):
                self.proofs(directory)
                source.write_bytes(b"after")
            with patch.object(gate, "ROOT", root), patch.object(gate, "INPUTS", ["input.c"]), \
                    patch.object(gate, "native_check", side_effect=mutate):
                with self.assertRaisesRegex(ValueError, "changed during"):
                    gate.verify(package, output)
            self.assertFalse(json.loads((output / "first-party-proof.json").read_text())["success"])

    def test_external_track_acknowledgement_and_release_are_required(self):
        for field in ["actualSelectedID", "externalAudioSelectedID", "repeatedRelease", "physicalOutputAccepted"]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); package = root / "payload"; package.mkdir(); output = root / "consumer"
                def incomplete(_, directory):
                    self.proofs(directory)
                    path = directory / "external-input-proof.json"
                    proof = json.loads(path.read_text()); proof[field] = not proof[field]
                    path.write_text(json.dumps(proof))
                with patch.object(gate, "native_check", side_effect=incomplete):
                    with self.assertRaisesRegex(ValueError, "incomplete"):
                        gate.verify(package, output)
                self.assertFalse(json.loads((output / "first-party-proof.json").read_text())["success"])

    def test_missing_external_track_proof_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); package = root / "payload"; package.mkdir(); output = root / "consumer"
            def missing(_, directory):
                self.proofs(directory)
                (directory / "external-input-proof.json").unlink()
            with patch.object(gate, "native_check", side_effect=missing):
                with self.assertRaises(FileNotFoundError):
                    gate.verify(package, output)
            self.assertFalse(json.loads((output / "first-party-proof.json").read_text())["success"])


if __name__ == "__main__":
    unittest.main()
