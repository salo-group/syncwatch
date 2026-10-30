"""Observe decoded PCM and subtitle pixels through the real native adapter."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from fixtures import create, embedded_tracks, external_tracks
from source_profile import ROOT, profile_data


def check_outputs(context, package, output, environment):
    directory = output / "decoded-output"
    directory.mkdir()
    proof = {"success": False, "physicalOutputAccepted": False, "cases": {}}
    try:
        # Keep every native ownership/control function unchanged. Only replace
        # the headless sinks so the probe can measure decoded frames and PCM.
        source = (ROOT / "packages/playback-native/src/playback.c").read_text()
        replacements = [('"dummy", "macosx"', '"vmem", "macosx"'),
                        ('"--aout=dummy"', '"--aout=amem"')]
        for before, after in replacements:
            if source.count(before) != 1:
                raise ValueError("Native callback-output instrumentation changed")
            source = source.replace(before, after)
        (directory / "derived-playback.c").write_text(source)
        proof["nativeSourceSha256"] = hashlib.sha256(
            (ROOT / "packages/playback-native/src/playback.c").read_bytes()).hexdigest()
        proof["callbackSinksOnly"] = True
        source_directory = ROOT / "packages/playback-native/src"
        executable = directory / ("output-probe.exe" if context.target == "windows-x64" else "output-probe")
        command = [context.tool(context.env["CC"]), "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                   "-DSW_VLC_REVISION=" + json.dumps(profile_data()["sources"]["vlc"]["revision"]),
                   "-I" + str(ROOT / "tools/vlc"), "-I" + str(ROOT / "3rdparty/vlc/include"),
                   "-I" + str(source_directory), "-I" + str(directory),
                   str(ROOT / "tools/playback/output_probe.c"), str(source_directory / "media_input.c"),
                   "-o", str(executable)]
        if context.target == "windows-x64":
            command.append("-static-libgcc")
        else:
            command.append("-lm")
            if context.target == "linux-x64":
                command.append("-ldl")
        context.run("native-decoded-output", command)
        media = embedded_tracks(directory)
        external_media = create(directory)
        external = external_tracks(directory)
        library = package / ("bin/libvlc.dll" if context.target == "windows-x64" else
                             "lib/libvlc.dylib" if context.target == "macos-arm64" else "lib/libvlc.so.5")
        for name, fixture, arguments in [
                ("embedded", media, []),
                ("external", external_media, [str(external["external-audio"]), str(external["srt"])])]:
            with (output / ("decoded-" + name + ".json")).open("wb") as log, (
                    output / ("decoded-" + name + "-stderr.log")).open("wb") as errors:
                result = subprocess.run([str(executable), str(fixture), str(library), *arguments],
                                        stdin=subprocess.DEVNULL, stdout=log, stderr=errors,
                                        env=environment, timeout=30)
            if result.returncode:
                raise ValueError("Native decoded output failed: " + name)
            actual = json.loads((output / ("decoded-" + name + ".json")).read_bytes())
            if (actual.get("success") is not True or actual.get("cycles") != 3
                    or not 840 <= actual.get("alternateToneHz", 0) <= 920
                    or actual.get("whiteSubtitlePixels", 0) < 20
                    or actual.get("selectedStreamsRetained") is not True
                    or actual.get("normalClose") is not True
                    or actual.get("physicalOutputAccepted") is not False):
                raise ValueError("Incomplete native decoded output proof: " + name)
            proof["cases"][name] = actual
        proof["success"] = True
    finally:
        # Each bounded child is joined before its authored media and executable
        # are released; the production SDK is read-only throughout.
        shutil.rmtree(directory)
        proof["temporaryFixturesRemoved"] = True
        (output / "decoded-output-proof.json").write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n")
    return proof
