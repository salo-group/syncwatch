"""Fresh first-party playback ABI verification against an existing native SDK."""

import contextlib
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[2]
INPUTS = ["packages/playback-native/include/syncwatch_playback.h",
          "packages/playback-native/include/syncwatch_media_input.h",
          "packages/playback-native/src/library.c", "packages/playback-native/src/playback.c",
          "packages/playback-native/src/media_input.c", "packages/playback-native/src/vlc_api.h",
          "tools/playback/build.py", "tools/playback/native_probe.c",
          "tools/playback/media_input_checks.py", "tools/playback/player_input_checks.py",
          "tools/playback/external_input_checks.py", "tools/playback/output_checks.py", "tools/playback/output_probe.c",
          "tools/playback/appkit_code_lifetime.py", "tools/playback/appkit_module_probe.c",
          "tools/playback/sdk_check.py", "tools/vlc/probe_common.h", "tools/vlc/owned_libraries.h",
          "tools/vlc/fixtures.py", "tools/vlc/media/embedded-tracks.json", "tools/vlc/media/embedded-tracks.b64"]


def native_check(package, output):
    spec = importlib.util.spec_from_file_location("syncwatch_playback_build", ROOT / "tools/playback/build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.check(package, output)


def verify(package, output):
    package, output = package.resolve(), output.resolve()
    if not package.is_dir():
        raise ValueError("Existing native SDK payload is required")
    if output == package or output.is_relative_to(package):
        raise ValueError("First-party verification cannot write into the SDK payload")
    output.mkdir(parents=True, exist_ok=False)
    inputs = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in INPUTS}
    proof = {"success": False, "sources": inputs, "applicationIntegrationReady": False,
             "physicalOutputAccepted": False}
    try:
        with (output / "verification.log").open("w", encoding="utf-8") as log, contextlib.redirect_stdout(log):
            native_check(package, output)
        actual = {name: json.loads((output / filename).read_bytes()) for name, filename in [
            ("playback", "native-proof.json"), ("input", "media-input-proof.json"),
            ("loadedInput", "player-input-proof.json"), ("externalInput", "external-input-proof.json"), ("decodedOutput", "decoded-output-proof.json")]}
        decoded = actual["decodedOutput"]
        cases = decoded.get("cases", {})
        decoded_valid = (decoded.get("success") is True and decoded.get("callbackSinksOnly") is True
                         and decoded.get("physicalOutputAccepted") is False
                         and decoded.get("temporaryFixturesRemoved") is True
                         and decoded.get("nativeSourceSha256") == inputs["packages/playback-native/src/playback.c"]
                         and set(cases) == {"embedded", "external"}
                         and all(case.get("success") is True and case.get("cycles") == 3
                                 and 840 <= case.get("alternateToneHz", 0) <= 920
                                 and 20 <= case.get("whiteSubtitlePixels", 0) < 320 * 180 / 4
                                 and case.get("selectedStreamsRetained") is True
                                 and case.get("normalClose") is True
                                 and case.get("physicalOutputAccepted") is False for case in cases.values()))
        if (actual["playback"].get("abiBytes") != 80 or actual["playback"].get("actualClock") is not True or
                actual["playback"].get("repeatedRelease") is not True or
                actual["playback"].get("physicalOutputAccepted") is not False or
                actual["input"].get("versionBytes") != 64 or actual["input"].get("noHandleGrowth") is not True or
                not decoded_valid or
                actual["loadedInput"].get("repeatedPausedSeekResume") != 3 or
                actual["loadedInput"].get("loadedContentHash") is not True or
                actual["loadedInput"].get("positionalReadsPreservePlayback") is not True or
                actual["loadedInput"].get("physicalOutputAccepted") is not False or
                actual["externalInput"].get("actualSelectedID") is not True or
                actual["externalInput"].get("externalAudioSelectedID") is not True or
                actual["externalInput"].get("repeatedRelease") is not True or
                actual["externalInput"].get("physicalOutputAccepted") is not False):
            raise ValueError("First-party native playback proof is incomplete")
        proof.update(actual)
        if inputs != {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in INPUTS}:
            raise ValueError("First-party playback sources changed during verification")
        proof["success"] = True
    finally:
        # Each child is already joined by build.check, including its timeout path.
        # Keep the current bridge and receipts, release authored fixtures/probes.
        for name in ["owned-i420-pcm.avi", "invalid.avi", "playback-probe", "playback-probe.exe"]:
            path = output / name
            if path.exists():
                if not path.is_file() or path.is_symlink():
                    raise ValueError("Unexpected native verification fixture")
                path.unlink()
        for pattern in ["owned-input-*", "owned-player-input-*"]:
            for path in output.glob(pattern):
                if path.is_symlink():
                    path.unlink()
                else:
                    shutil.rmtree(path)
        proof["temporaryFixturesRemoved"] = True
        (output / "first-party-proof.json").write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n")
    return proof
