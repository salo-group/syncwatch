"""Compile a fresh native consumer and exercise the relocated owned media SDK."""

import json
import os
from pathlib import Path
import subprocess

from fixtures import create, encoded, external_tracks, embedded_tracks, sparse_video
from source_profile import ROOT, profile_data
from package_report import validate_seek_reset


def environment(package, target):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DYLD_", "LD_", "VLC_"))}
    libraries = package / ("bin" if target == "windows-x64" else "lib")
    if target == "windows-x64":
        env["PATH"] = str(libraries) + os.pathsep + str(Path(os.environ["SystemRoot"]) / "System32")
    elif target == "linux-x64":
        env["FONTCONFIG_FILE"] = str(package / "share/fonts/fonts.conf")
        env["FONTCONFIG_PATH"] = str(package / "share/fonts")
        env["PULSE_CLIENTCONFIG"] = str(package / "share/pulse/client.conf")
    return env


def compile_consumer(context, package, directory, source, name):
    suffix = ".exe" if context.target == "windows-x64" else ""
    executable = directory / (name + suffix)
    libraries = package / ("bin" if context.target == "windows-x64" else "lib")
    libname = {"windows-x64": "libvlc.dll", "linux-x64": "libvlc.so.5", "macos-arm64": "libvlc.dylib"}[context.target]
    actual = libraries / libname
    if not actual.is_file():
        raise ValueError("Missing public libVLC runtime library")
    command = [context.tool(context.env["CC"]), "-std=c11", "-Wall", "-Wextra", "-Werror",
        "-DSW_VLC_REVISION=" + json.dumps(profile_data()["sources"]["vlc"]["revision"]),
        "-I" + str(package / "include"), str(ROOT / "tools/vlc" / source),
        "-L" + str(package / "lib"), "-lvlc", "-o", str(executable)]
    if context.target == "windows-x64":
        command.extend(["-static-libgcc", "-static-libstdc++"])
    else:
        command.append("-Wl,-rpath," + str(libraries))
        if context.target == "linux-x64":
            command.append("-ldl")
    context.run(name + "-compile", command)
    return executable, actual


def verify(context, package):
    directory = context.work / "consumer"
    directory.mkdir()
    media = create(directory)
    executable, actual = compile_consumer(context, package, directory, "probe.c", "vlc-probe")
    env = environment(package, context.target)
    fixtures = {"i420-pcm": (media, {"videoCodec": "30323449", "audioCodec": "6c363173", "width": 320, "height": 180})}
    fixtures.update(encoded(directory))
    results = {}
    for name, (fixture, metadata) in fixtures.items():
        output = directory / (name + "-proof.log")
        errors = directory / (name + "-stderr.log")
        with output.open("wb") as log, errors.open("wb") as diagnostics:
            process = subprocess.run([str(executable), str(fixture), str(actual), str(metadata["width"]), str(metadata["height"])], env=env,
                stdin=subprocess.DEVNULL, stdout=log, stderr=diagnostics, timeout=60)
        if process.returncode:
            raise ValueError("Relocated media probe failed:\n" + output.read_text(errors="replace") + errors.read_text(errors="replace"))
        result = json.loads(output.read_text())
        expected = ["metadata", "decodeVideo", "decodeAudio", "play", "pause", "seekPixels", "resume", "stop", "reload", "release"]
        if (result.get("cases") != expected or result.get("ownedLibraryLoaded") is not True or
                result.get("physicalOutputAccepted") is not False or result.get("version") != "3.0.24" or
                any(result.get(key) != metadata[key] for key in ["videoCodec", "audioCodec"])):
            raise ValueError("Native media proof differs from its required behavior")
        results[name] = result
    effects = verify_effects(context, package, directory, media)
    return {"media": results, "effects": effects, "seekReset": verify_seek_reset(context, package, directory)}


def verify_seek_reset(context, package, directory):
    fixture = directory / "owned-sparse-video.mkv"
    executable = directory / ("seek-reset-probe.exe" if context.target == "windows-x64" else "seek-reset-probe")
    try:
        fixture = sparse_video(directory)
        executable, actual = compile_consumer(context, package, directory, "seek_probe.c", "seek-reset-probe")
        output, errors = directory / "seek-reset-proof.json", directory / "seek-reset-stderr.log"
        with output.open("wb") as log, errors.open("wb") as diagnostics:
            process = subprocess.run([str(executable), str(fixture), str(actual)],
                env=environment(package, context.target), stdin=subprocess.DEVNULL,
                stdout=log, stderr=diagnostics, timeout=45)
        if process.returncode:
            raise ValueError("Native decoder seek reset failed: " + errors.read_text(errors="replace"))
        result = json.loads(output.read_bytes())
    finally:
        # subprocess.run joins the child on failure and timeout as well.
        for path in [fixture, executable]:
            path.unlink(missing_ok=True)
    result["temporaryFixturesRemoved"] = True
    validate_seek_reset(result)
    return result


def verify_effects(context, package, directory, media):
    executable, actual = compile_consumer(context, package, directory, "effects_probe.c", "effects-probe")
    external = external_tracks(directory)
    embedded = embedded_tracks(directory)
    env = environment(package, context.target)
    if context.target == "linux-x64":
        env["XDG_CACHE_HOME"] = str(directory / "font-cache")
    results = {}
    for name in ["adjust", "normalize", "tempo", "external-audio", "srt", "ass", "embedded-audio", "embedded-srt", "embedded-ass"]:
        args = [str(executable), name, str(embedded if name.startswith("embedded-") else media), str(actual)]
        if name in external:
            args.append(external[name].resolve().as_uri())
        output, errors = directory / (name + "-proof.json"), directory / (name + "-stderr.log")
        with output.open("wb") as log, errors.open("wb") as diagnostics:
            process = subprocess.run(args, env=env, stdin=subprocess.DEVNULL,
                                     stdout=log, stderr=diagnostics, timeout=30)
        if process.returncode:
            raise ValueError("Native media effect failed:\n" + errors.read_text(errors="replace"))
        result = json.loads(output.read_bytes())
        if (result.get("case") != name or result.get("ownedLibraryLoaded") is not True or
                result.get("physicalOutputAccepted") is not False):
            raise ValueError("Native media effect proof differs from its required behavior")
        results[name] = result
    return results
