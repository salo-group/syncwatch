"""Build, exercise and retain development playback consumers of the owned SDK."""

import argparse
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile

from build import ROOT, check as native_check, runtime_environment
from consumers import check as consumer_check, run, verify_package


def target():
    return {("Darwin", "arm64"): "macos-arm64", ("Windows", "AMD64"): "windows-x64",
            ("Linux", "x86_64"): "linux-x64"}[(platform.system(), platform.machine())]


def provision():
    selected = target()
    (ROOT / "artifacts").mkdir(exist_ok=True)
    if selected == "linux-x64":
        subprocess.run([sys.executable, "tools/jvm/jdk.py", "--provision"], cwd=ROOT, check=True)
    elif selected == "windows-x64":
        spec = importlib.util.spec_from_file_location("playback_server_sdk", ROOT / "tools/server/check.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        specification = json.loads((ROOT / "tools/server/toolchains.json").read_text())
        temporary = Path(os.environ.get("RUNNER_TEMP", str(ROOT / ".local/toolchains"))) / "syncwatch-playback-sdk"
        executable = module.provision(specification, "win-x64", temporary)
        (ROOT / "artifacts/playback-sdk.json").write_text(json.dumps({"dotnet": str(executable)}) + "\n")


def candidate(package, output, bridge, binary_directory):
    def write(archive, source, name):
        # Original grant/source mtimes may precede ZIP's epoch. Preserve bytes
        # and executable permissions with an explicit reproducible ZIP date.
        info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
        info.create_system = 3
        info.external_attr = (source.stat().st_mode & 0xFFFF) << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        with source.open("rb") as data, archive.open(info, "w") as destination:
            shutil.copyfileobj(data, destination)
    selected = target()
    with zipfile.ZipFile(output / "candidate.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
        if selected == "macos-arm64":
            prefix = "SyncWatch.app/Contents/"
            write(archive, binary_directory / "SyncWatch", prefix + "MacOS/SyncWatch")
            write(archive, ROOT / "apps/macos/Resources/Info.plist", prefix + "Info.plist")
            media_prefix = prefix + "Resources/Media/"
        elif selected == "windows-x64":
            for path in sorted(binary_directory.rglob("*")):
                if path.is_file():
                    write(archive, path, "SyncWatch/" + path.relative_to(binary_directory).as_posix())
            write(archive, bridge, "SyncWatch/" + bridge.name)
            media_prefix = "SyncWatch/Media/"
        else:
            for path in sorted((output / "consumers/classes").rglob("*.class")):
                write(archive, path, "DevelopmentJVM/classes/" + path.relative_to(output / "consumers/classes").as_posix())
            for path in [bridge, output / "consumers/syncwatch_playback_jni.so"]:
                write(archive, path, "DevelopmentJVM/" + path.name)
            media_prefix = "DevelopmentJVM/Media/"
        for path in sorted(package.rglob("*")):
            if path.is_file():
                write(archive, path, media_prefix + path.relative_to(package).as_posix())
        archive.writestr("Sources/first-party.tar", subprocess.check_output(["git", "archive", "HEAD"], cwd=ROOT))
        write(archive, ROOT / "LICENSE", "LICENSE")
        archive.writestr("DEVELOPMENT.txt", "SyncWatch local playback development candidate.\n"
            "The Media directory includes the complete source, grants, recipe and integrity receipt.\n"
            "Use --local-playback with an absolute local movie path. Normal room activation remains gated.\n"
            "Windows requires the pinned developer .NET runtime. Linux contains a JNI development boundary, not a GUI.\n"
            "This is not a signed, authenticated or immutable release installation. Physical output is unqualified.\n")
    with zipfile.ZipFile(output / "candidate.zip") as archive:
        if archive.testzip() is not None or len(archive.namelist()) != len(set(archive.namelist())):
            raise ValueError("Development candidate ZIP is incomplete or ambiguous")
        receipt = json.loads(archive.read(media_prefix + "build-report.json"))
        actual = {name[len(media_prefix):]: hashlib.sha256(archive.read(name)).hexdigest()
                  for name in archive.namelist() if name.startswith(media_prefix) and name != media_prefix + "build-report.json"}
        if actual != receipt["files"]:
            raise ValueError("Development candidate changed or omitted media sources, grants or binaries")


def verify(dotnet=None, gui=False, package=None):
    selected = target()
    output = ROOT / "artifacts/playback" / selected
    output.mkdir(parents=True, exist_ok=True)
    package = (package or ROOT / "artifacts/dependencies/libvlc" / selected / "payload").resolve()
    manifest = {"schemaVersion": 1, "source": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "sourceDirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)),
                "target": selected, "success": False, "physicalOutputAccepted": False,
                "releaseReady": False, "linuxGuiAccepted": False}
    try:
        receipt = verify_package(package)
        if receipt["sdk"]["target"] != selected:
            raise ValueError("Media SDK target differs from the application host")
        manifest["mediaSdk"] = receipt["sdk"]
        bridge = native_check(package, output / "native")
        env = runtime_environment(package, output / "native")
        binary_directory = None
        if selected == "macos-arm64":
            run(["swift", "build", "--package-path", "apps/macos", "-Xswiftc", "-warnings-as-errors"], output, "app-build", env)
            binary_directory = Path(subprocess.check_output(["swift", "build", "--package-path", "apps/macos", "--show-bin-path"], cwd=ROOT, text=True).strip())
        elif selected == "windows-x64":
            dotnet = dotnet or json.loads((ROOT / "artifacts/playback-sdk.json").read_bytes())["dotnet"]
            consumer_check(package, output / "consumers", dotnet=dotnet)
            run([dotnet, "build", "apps/windows/SyncWatch", "--configuration", "Debug", "--nologo",
                 "-m:1", "/p:UseSharedCompilation=false"], output, "app-build", env)
            binary_directory = ROOT / "apps/windows/SyncWatch/bin/Debug/net10.0-windows"
        else:
            consumer_check(package, output / "consumers", jdk_root=ROOT)
            # Both native objects must be adjacent in the relocated development candidate.
            bridge = output / "consumers" / bridge.name
        candidate(package, output, bridge, binary_directory)
        if gui and selected != "linux-x64":
            # Launch the relocated candidate with its default owned Media path;
            # an unbundled SDK path cannot accidentally satisfy this check.
            with tempfile.TemporaryDirectory(prefix="relocated-", dir=output) as temporary:
                relocated = Path(temporary)
                with zipfile.ZipFile(output / "candidate.zip") as archive:
                    archive.extractall(relocated)
                    if os.name != "nt":
                        for member in archive.infolist():
                            if not member.is_dir():
                                (relocated / member.filename).chmod((member.external_attr >> 16) & 0o777)
                command = ([str(relocated / "SyncWatch.app/Contents/MacOS/SyncWatch")] if selected == "macos-arm64"
                           else [dotnet, str(relocated / "SyncWatch/SyncWatch.dll")])
                for mode in (["normal", "detach"] if selected == "windows-x64" else ["normal"]):
                    report = output / (mode + "-app-proof.json")
                    run([*command, "--local-playback", str(output / "native/owned-i420-pcm.avi"),
                         "--playback-report", str(report),
                         *(["--playback-detach-probe"] if mode == "detach" else [])], output, mode + "-app", env)
                    proof = json.loads(report.read_bytes())
                    if not proof.get("success") or not proof.get("actualNativeSurface") or proof["physicalOutputAccepted"]:
                        raise ValueError("Owned native app surface acceptance failed")
            manifest["nativeAppSurfaceAccepted"] = True
        else:
            manifest["nativeAppSurfaceAccepted"] = False
        manifest["success"] = True
    finally:
        (output / "acceptance.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        # Retain raw diagnostics even when the actual consumer fails.
        with zipfile.ZipFile(output.parent / (selected + ".zip"), "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(output.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(output).as_posix(),
                                  compress_type=zipfile.ZIP_STORED if path.name == "candidate.zip" else zipfile.ZIP_DEFLATED)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("provision", "check"))
    parser.add_argument("--dotnet")
    parser.add_argument("--package", type=Path, help="An existing verified source-built development SDK payload")
    parser.add_argument("--gui", action="store_true", help="Require real AppKit/WPF surface acceptance on a GUI host")
    args = parser.parse_args()
    provision() if args.operation == "provision" else verify(args.dotnet, args.gui, args.package)
