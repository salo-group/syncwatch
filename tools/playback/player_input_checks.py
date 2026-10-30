"""Exercise VLC and scoped content reads from one owned local file object."""

import ctypes
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time

from media_input_checks import Version


class Observation(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in ["generation", "sequence"]] + [
        (name, ctypes.c_int64) for name in ["position_ms", "duration_ms"]] + [
        ("rate", ctypes.c_double)] + [(name, ctypes.c_int32) for name in [
            "status", "paused", "seekable", "buffering", "ended", "video_outputs",
            "width", "height", "volume", "muted"]]


def check_player_input(library, libvlc, revision, fixture, output):
    api = ctypes.CDLL(str(library))
    handle, generation = ctypes.c_void_p, ctypes.c_uint64
    signatures = {
        "sw_playback_open": [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int, handle, ctypes.POINTER(handle)],
        "sw_playback_load": [handle, generation, ctypes.c_char_p],
        "sw_playback_input_version": [handle, generation, ctypes.POINTER(Version)],
        "sw_playback_read_at": [handle, generation, ctypes.c_uint64, ctypes.c_void_p,
                                ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)],
        "sw_playback_observe": [handle, generation, ctypes.POINTER(Observation)],
        "sw_playback_seek": [handle, generation, ctypes.c_int64],
        "sw_playback_pause": [handle, generation, ctypes.c_int],
        "sw_playback_stop": [handle, generation],
    }
    for name, arguments in signatures.items():
        function = getattr(api, name)
        function.argtypes, function.restype = arguments, ctypes.c_int
    api.sw_playback_close.argtypes, api.sw_playback_close.restype = [handle], None
    assert ctypes.sizeof(Observation) == 80
    proof = {"platform": os.name, "physicalOutputAccepted": False}
    player = handle()
    content = Path(fixture).read_bytes()
    with tempfile.TemporaryDirectory(prefix="owned-player-input-", dir=output) as temporary:
        media = Path(temporary).resolve() / "Movie кино 映画.avi"
        media.write_bytes(content)
        encoded = str(media).encode("utf-8")

        def observation(scope):
            value = Observation()
            assert api.sw_playback_observe(player, scope, ctypes.byref(value)) == 0
            return value

        def ready(scope, position=0):
            until = time.monotonic() + 12
            while time.monotonic() < until:
                value = observation(scope)
                assert value.status != 3, "Loaded native file failed"
                if value.status == 2 and value.paused and abs(value.position_ms - position) <= 250:
                    assert 3800 <= value.duration_ms <= 4200 and value.seekable
                    return value
                time.sleep(.02)
            raise AssertionError("Bounded pinned-input load/seek did not become ready")

        def load(scope):
            assert api.sw_playback_load(player, scope, encoded) == 0
            return ready(scope)

        def version(scope, expected=0):
            value = Version()
            assert api.sw_playback_input_version(player, scope, ctypes.byref(value)) == expected
            if expected:
                assert list(value.words) == [0] * 8
            return value

        def read(scope, offset, capacity=262144, expected=0):
            buffer = ctypes.create_string_buffer(min(capacity, 262144))
            count = ctypes.c_size_t(123)
            assert api.sw_playback_read_at(player, scope, offset, buffer, capacity, ctypes.byref(count)) == expected
            if expected:
                assert count.value == 0
            return buffer.raw[:count.value]

        try:
            assert api.sw_playback_open(str(libvlc).encode("utf-8"), revision.encode("ascii"),
                                        0, None, ctypes.byref(player)) == 0
            version(0, 2)
            read(0, 0, expected=2)
            load(1)
            before = version(1)
            assert before.words[3] == len(content)
            # A paused seek/stream refill must not strand the native input on
            # resume. Observe progress and a subsequent pause, not just Playing.
            for _ in range(3):
                for target in (1300, 0, 1300, 0, 1300):
                    assert api.sw_playback_seek(player, 1, target) == 0
                    ready(1, target)
                start = observation(1).position_ms
                assert api.sw_playback_pause(player, 1, 0) == 0
                until = time.monotonic() + 4
                while True:
                    actual = observation(1)
                    assert actual.status != 3 and not actual.ended
                    if actual.status == 2 and not actual.paused and actual.position_ms > start + 250:
                        break
                    assert time.monotonic() < until, "Repeated paused seeks stalled native resume"
                    time.sleep(.02)
                assert api.sw_playback_pause(player, 1, 1) == 0
                until = time.monotonic() + 4
                while not observation(1).paused:
                    assert time.monotonic() < until, "Native pause did not settle after resume"
                    time.sleep(.02)
            proof["repeatedPausedSeekResume"] = 3
            assert api.sw_playback_seek(player, 1, 1300) == 0
            position = ready(1, 1300).position_ms
            digest = hashlib.sha256()
            for offset in range(0, len(content), 262144):
                digest.update(read(1, offset))
            assert digest.digest() == hashlib.sha256(content).digest()
            assert list(version(1).words) == list(before.words)
            assert abs(observation(1).position_ms - position) <= 60
            assert read(1, len(content)) == b""
            read(1, len(content) + 1, expected=1)
            read(1, 0, 262145, 1)
            version(0, 3)
            read(0, 0, expected=3)
            proof.update(loadedContentHash=True, positionalReadsPreservePlayback=True,
                         scopedVersion=True, staleReadsRejected=True, unicodeLoadedFile=True)
            if os.name == "nt":
                for operation in [lambda: media.write_bytes(b"x" * len(content)),
                                  lambda: media.rename(media.with_suffix(".moved"))]:
                    try:
                        operation()
                    except PermissionError:
                        pass
                    else:
                        raise AssertionError("Loaded input allowed write or replacement")
                # A replacement load must release its previous sharing lease.
                load(2)
                assert api.sw_playback_stop(player, 2) == 0
                assert api.sw_playback_stop(player, 2) == 0
                media.write_bytes(content)
                load(3)
                proof["windowsLeaseAndRelease"] = True
            else:
                modified = media.stat().st_mtime_ns
                original = media.with_suffix(".original")
                media.rename(original)
                media.write_bytes(content)
                os.utime(media, ns=(media.stat().st_atime_ns, modified))
                version(1, 2)
                assert observation(1).status == 3
                assert api.sw_playback_pause(player, 1, 0) == 2
                os.replace(original, media)
                version(1, 2)  # Restoring the original path cannot revive admission.
                load(2)
                modified = media.stat().st_mtime_ns
                with media.open("r+b") as file:
                    file.seek(len(content) - 1)
                    file.write(bytes([content[-1] ^ 1]))
                os.utime(media, ns=(media.stat().st_atime_ns, modified))
                read(2, 0, expected=2)
                assert observation(2).status == 3
                media.write_bytes(content)
                load(3)
                proof.update(replacementFailsLoadedInput=True, restoredMtimeFailsLoadedInput=True,
                             invalidatedPlaybackCannotResume=True)
            assert api.sw_playback_stop(player, 3) == 0
            version(3, 2)
            read(3, 0, expected=2)
            proof["stoppedContentUnavailable"] = True
        finally:
            if player:
                api.sw_playback_close(player)
    assert not Path(temporary).exists()
    proof["temporaryFilesRemoved"] = True
    (Path(output) / "player-input-proof.json").write_text(json.dumps(proof, indent=2) + "\n")
    return proof


if __name__ == "__main__":
    import sys
    print(json.dumps(check_player_input(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3],
                                       Path(sys.argv[4]), Path(sys.argv[5])), sort_keys=True))
