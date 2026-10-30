"""Compile the first-party playback ABI with the selected native media SDK tools."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/vlc"))
from build_context import BuildContext
from source_profile import profile_data


def build(directory):
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    # This adapter consumes an existing SDK and compiles C directly. It does
    # not regenerate a dependency's configure scripts or require Autotools.
    context = BuildContext(directory / "sdk", profile_data()["sourceDateEpoch"], autotools=False)
    suffix = {"macos-arm64": ".dylib", "windows-x64": ".dll", "linux-x64": ".so"}[context.target]
    library = directory / ("syncwatch_playback" + suffix)
    sources = ROOT / "packages/playback-native/src"
    common = [context.tool(context.env["CC"]), "-std=c11", "-O2", "-g0", "-Wall", "-Wextra", "-Werror"]
    command = [*common, "-shared", "-fvisibility=hidden", str(sources / "library.c"), str(sources / "playback.c"),
               str(sources / "media_input.c"), "-o", str(library)]
    if context.target == "windows-x64":
        command += ["-static-libgcc"]
    else:
        command += ["-fPIC", "-lm"]
        if context.target == "macos-arm64":
            command += ["-Wl,-install_name,@rpath/" + library.name]
        else:
            command += ["-Wl,-soname," + library.name]
        if context.target == "linux-x64":
            command += ["-ldl"]
    context.run("playback-abi", command)
    executable = directory / ("playback-probe.exe" if os.name == "nt" else "playback-probe")
    command = [*common, "-D_POSIX_C_SOURCE=200809L", str(ROOT / "tools/playback/native_probe.c"), str(library), "-o", str(executable)]
    if context.target == "windows-x64":
        command += ["-static-libgcc"]
    else:
        command += ["-Wl,-rpath," + str(directory)]
    context.run("playback-probe", command)
    return context, library, executable


def runtime_environment(package, output):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DYLD_", "LD_", "VLC_"))}
    if os.name == "nt":
        env["PATH"] = str(output) + os.pathsep + str(Path(os.environ["SystemRoot"]) / "System32")
    if sys.platform == "linux":
        env["FONTCONFIG_FILE"] = str(package / "share/fonts/fonts.conf")
        env["FONTCONFIG_PATH"] = str(package / "share/fonts")
        env["PULSE_CLIENTCONFIG"] = str(package / "share/pulse/client.conf")
        env["XDG_CACHE_HOME"] = str(output / "font-cache")
    return env


def check(package, output):
    context, library, executable = build(output)
    if sys.platform == "darwin":
        spec = importlib.util.spec_from_file_location("syncwatch_appkit_code_lifetime", ROOT / "tools/playback/appkit_code_lifetime.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.check(context, output)
    # A separate process releases the test DLL mapping before Windows cleanup.
    with (output / "media-input-check.log").open("wb") as log:
        subprocess.run([sys.executable, str(ROOT / "tools/playback/media_input_checks.py"),
                        str(library), str(output)], stdout=log, stderr=subprocess.STDOUT,
                       stdin=subprocess.DEVNULL, check=True, timeout=30)
    from fixtures import create
    media = create(output)
    invalid_media = output / "invalid.avi"
    invalid_media.write_bytes(b"authored invalid media fixture")
    libvlc = package / ("bin/libvlc.dll" if os.name == "nt" else "lib/libvlc.dylib" if sys.platform == "darwin" else "lib/libvlc.so.5")
    env = runtime_environment(package, output)
    with (output / "native-proof.json").open("wb") as log, (output / "native-stderr.log").open("wb") as errors:
        subprocess.run([str(executable), str(libvlc.resolve()), profile_data()["sources"]["vlc"]["revision"], str(media.resolve()), str(invalid_media)],
                       stdin=subprocess.DEVNULL, stdout=log, stderr=errors, env=env, timeout=180, check=True)
    proof = json.loads((output / "native-proof.json").read_bytes())
    assert proof["abiBytes"] == 80 and proof["repeatedRelease"] and proof["failedMedia"] and proof["physicalOutputAccepted"] is False
    with (output / "player-input-check.log").open("wb") as log:
        subprocess.run([sys.executable, str(ROOT / "tools/playback/player_input_checks.py"),
                        str(library), str(libvlc.resolve()), profile_data()["sources"]["vlc"]["revision"],
                        str(media), str(output)], stdout=log, stderr=subprocess.STDOUT,
                       stdin=subprocess.DEVNULL, check=True, env=env, timeout=90)
    with (output / "external-input-check.log").open("wb") as log:
        subprocess.run([sys.executable, str(ROOT / "tools/playback/external_input_checks.py"),
                        str(library), str(libvlc.resolve()), profile_data()["sources"]["vlc"]["revision"],
                        str(media), str(output)], stdout=log, stderr=subprocess.STDOUT,
                       stdin=subprocess.DEVNULL, check=True, env=env, timeout=90)
    from output_checks import check_outputs
    check_outputs(context, package, output, env)
    print(json.dumps(proof, sort_keys=True))
    return library


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    check(args.package.resolve(), args.output.resolve())
