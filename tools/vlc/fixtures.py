"""Create deterministic, first-party media fixtures without an external encoder."""

import argparse
import base64
import hashlib
import json
import lzma
import math
from pathlib import Path
import struct
import wave

WIDTH, HEIGHT, FPS, SECONDS, RATE = 320, 180, 25, 4, 48000


def chunk(kind, data):
    return kind + struct.pack("<I", len(data)) + data + (b"\0" if len(data) % 2 else b"")


def listing(kind, data):
    return chunk(b"LIST", kind + data)


def avi():
    frames = FPS * SECONDS
    image_size = WIDTH * HEIGHT * 3 // 2
    audio_size = RATE // FPS * 4
    main = struct.pack("<14I", 1000000 // FPS, (image_size + audio_size) * FPS,
                       0, 0x10, frames, 0, 2, image_size, WIDTH, HEIGHT, 0, 0, 0, 0)
    video_header = struct.pack("<4s4sIHH8I4h", b"vids", b"I420", 0, 0, 0,
                               0, 1, FPS, 0, frames, image_size, 0xffffffff, 0,
                               0, 0, WIDTH, HEIGHT)
    video_format = struct.pack("<IiiHH4sIiiII", 40, WIDTH, HEIGHT, 1, 12, b"I420",
                               image_size, 0, 0, 0, 0)
    audio_header = struct.pack("<4s4sIHH8I4h", b"auds", b"\0" * 4, 0, 0, 0,
                               0, 4, RATE * 4, 0, RATE * SECONDS,
                               audio_size, 0xffffffff, 4, 0, 0, 0, 0)
    audio_format = struct.pack("<HHIIHH", 1, 2, RATE, RATE * 4, 4, 16)
    headers = chunk(b"avih", main)
    headers += listing(b"strl", chunk(b"strh", video_header) + chunk(b"strf", video_format))
    headers += listing(b"strl", chunk(b"strh", audio_header) + chunk(b"strf", audio_format))
    media, index, offset = [], [], 4
    for number in range(frames):
        # Every decoded frame identifies its actual position through luminance.
        video = bytes([40 + number * 2]) * (WIDTH * HEIGHT)
        video += bytes([128]) * (WIDTH * HEIGHT // 2)
        samples = bytearray()
        for sample in range(RATE // FPS):
            position = number * (RATE // FPS) + sample
            value = round(7000 * math.sin(2 * math.pi * 440 * position / RATE))
            samples.extend(struct.pack("<hh", value, value))
        for kind, data in [(b"00dc", video), (b"01wb", bytes(samples))]:
            encoded = chunk(kind, data)
            media.append(encoded)
            index.append(struct.pack("<4sIII", kind, 0x10, offset, len(data)))
            offset += len(encoded)
    body = b"AVI " + listing(b"hdrl", headers) + listing(b"movi", b"".join(media))
    return chunk(b"RIFF", body + chunk(b"idx1", b"".join(index)))


def create(directory):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "owned-i420-pcm.avi"
    path.write_bytes(avi())
    return path


def external_tracks(directory):
    audio = directory / "external-880hz.wav"
    samples = bytearray()
    for position in range(RATE * SECONDS):
        value = round(3000 * math.sin(2 * math.pi * 880 * position / RATE))
        samples.extend(struct.pack("<hh", value, value))
    with wave.open(str(audio), "wb") as stream:
        stream.setparams((2, 2, RATE, RATE * SECONDS, "NONE", "not compressed"))
        stream.writeframes(samples)
    srt = directory / "external.srt"
    srt.write_bytes(b"1\n00:00:00,000 --> 00:00:03,900\nSYNCWATCH\n")
    ass = directory / "external.ass"
    ass.write_bytes(("[Script Info]\nScriptType: v4.00+\nPlayResX: 320\nPlayResY: 180\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, "
        "Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,24,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,2,10,10,10,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:00.00,0:00:03.90,Default,,0,0,0,,SYNCWATCH\n").encode())
    return {"external-audio": audio, "srt": srt, "ass": ass}


def encoded(directory):
    root = Path(__file__).resolve().parent / "media"
    profile = json.loads((root / "fixtures.json").read_text())
    result = {}
    for name, item in profile["fixtures"].items():
        data = base64.b64decode(b"".join((root / item["encoded"]).read_bytes().split()), validate=True)
        if len(data) != item["bytes"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError("First-party encoded media fixture changed")
        path = directory / (name + ".mp4")
        path.write_bytes(data)
        result[name] = (path, item)
    return result


def embedded_tracks(directory):
    return _authored_matroska(directory, "embedded-tracks.json", "owned-embedded-tracks.mkv")


def sparse_video(directory):
    return _authored_matroska(directory, "sparse-video.json", "owned-sparse-video.mkv")


def _authored_matroska(directory, metadata_name, filename):
    root = Path(__file__).resolve().parent / "media"
    metadata = json.loads((root / metadata_name).read_text())
    encoded = (root / metadata["encoded"]).read_bytes()
    if (metadata["schemaVersion"] != 1 or metadata["encoding"] != "base64+xz"
            or len(encoded) > 131072 or not 0 < metadata["bytes"] <= 32 * 1024 * 1024):
        raise ValueError("Invalid authored embedded-track fixture")
    compressed = base64.b64decode(b"".join(encoded.split()), validate=True)
    decoder = lzma.LZMADecompressor(memlimit=64 * 1024 * 1024)
    data = decoder.decompress(compressed, max_length=metadata["bytes"] + 1)
    if (not decoder.eof or decoder.unused_data or len(data) != metadata["bytes"]
            or hashlib.sha256(data).hexdigest() != metadata["sha256"]):
        raise ValueError("Authored embedded-track fixture changed")
    path = directory / filename
    path.write_bytes(data)
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    print(create(parser.parse_args().directory))
