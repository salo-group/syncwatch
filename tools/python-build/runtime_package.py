"""Own and revalidate the relocated interpreter SDK and its original inputs."""

import json
from pathlib import Path
import shutil
import sys
import tempfile

from binary_audit import audit
from build_metadata import BUILD_INFO_SUFFIX
from configuration import UNIX_MAKEFILE
from source_profile import ROOT, digest, encoded, notices, original_sources, stdlib_archive

sys.path.insert(0, str(ROOT / "tools/python-runtime"))
from native_probe import exercise_sdk


def layout(payload, target):
    windows = target == "windows-x64"
    name = {"macos-arm64": "libpython3.12.dylib", "linux-x64": "libpython3.12.so.1.0",
            "windows-x64": "python312.dll"}[target]
    runtime = payload / ("bin" if windows else "lib") / name
    return {"prefix": payload, "include": payload / "include", "runtime": runtime,
            "library": payload / "lib/python312.lib" if windows else runtime,
            "stdlib": payload / ("bin/python312.zip" if windows else "lib/python312.zip"),
            "extensions": runtime.parent}


def immutable_files(sources, profile):
    files = {"include/" + name.removeprefix("Include/"): data for name, data in sources["cpython"].items()
             if name.startswith("Include/") and name.endswith(".h")}
    files.update(notices(sources, profile))
    files["LICENSE"] = (ROOT / "LICENSE").read_bytes()
    files["sources.zip"] = original_sources(sources, profile)
    files["selection.json"] = encoded({"profile": profile["profile"], "version": profile["version"],
        "modules": profile["extensions"], "stdlib": profile["stdlib"],
        "configurationChanges": ["Selected builtin/extension membership", "Prefixed static zlib",
            "Relocatable shared-library identity", "Stable build metadata and relative bootstrap source path"],
        "interpreterAlgorithmsChanged": False, "syncplayRuntimeReady": False})
    return files


def inventory(payload):
    if payload.is_symlink():
        raise ValueError("Runtime payload must be an owned directory")
    files = {}
    for path in payload.rglob("*"):
        if path.is_symlink():
            raise ValueError("Runtime payload contains a symbolic link")
        if path.is_file():
            files[path.relative_to(payload).as_posix()] = digest(path.read_bytes())
    return files


def write_files(payload, files):
    for name, data in files.items():
        path = payload / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def package(payload, sources, profile, sdk, source, native, commands, options):
    paths = layout(payload, sdk["target"])
    files = immutable_files(sources, profile)
    files[paths["stdlib"].relative_to(payload).as_posix()] = stdlib_archive(sources, profile)
    files[paths["runtime"].relative_to(payload).as_posix()] = native["runtime"].read_bytes()
    files[paths["library"].relative_to(payload).as_posix()] = native["library"].read_bytes()
    files["include/pyconfig.h"] = native["config"].read_bytes()
    for path in native["extensions"]:
        files["bin/" + path.name] = path.read_bytes()
    # Configuration derivatives are separate from unchanged source originals.
    for name in ["Modules/getbuildinfo.c", "PC/config.c", "PCbuild/pythoncore.vcxproj",
                 "PCbuild/pcbuild.proj", "PCbuild/_socket.vcxproj", "PCbuild/select.vcxproj"]:
        data = (source / name).read_bytes()
        if data != sources["cpython"][name]:
            files["configuration/" + name] = data
    generated = source if sdk["target"] == "windows-x64" else source.parent / "build"
    if sdk["target"] != "windows-x64":
        for name in ["Makefile", "Modules/Setup.local"]:
            files["configuration/" + name] = (generated / name).read_bytes()
        files["configuration/" + UNIX_MAKEFILE.name] = UNIX_MAKEFILE.read_bytes()
    files["configuration/generated-source-hashes.json"] = encoded({
        path.relative_to(generated).as_posix(): digest(path.read_bytes())
        for path in sorted((generated / "Python").rglob("*"))
        if path.is_file() and (path.suffix == ".h" or path.name == "deepfreeze.c")})
    write_files(payload, files)
    proof = relocated_probe(payload, sdk["target"])
    report = {"schemaVersion": 1, "profileSha256": digest(encoded(profile)), "sdk": sdk,
              "commands": commands, "files": inventory(payload), "proof": proof,
              "nativeDependencies": audit(native, sdk["target"], sdk, options),
              "pinnedRuntimeBuilt": True, "pinnedEmbeddingAbiVerified": True,
              "syncplayRuntimeReady": False}
    write_files(payload, {"build-report.json": encoded(report)})
    return report


def relocated_probe(payload, target):
    # Actual C startup links the relocated owned library, headers and stdlib.
    # The development Python only orchestrates the compiler and child processes.
    with tempfile.TemporaryDirectory(prefix="sw-owned-runtime-") as temporary:
        owned = Path(temporary) / "runtime-\u03bc-\u0442\u0435\u0441\u0442"
        shutil.copytree(payload, owned)
        return exercise_sdk(layout(owned, target),
                            (ROOT / "tools/python-build/checks.py").read_text(encoding="utf-8"),
                            build_info_suffix=BUILD_INFO_SUFFIX)


def verify(payload, sources, profile, sdk, options):
    report = json.loads((payload / "build-report.json").read_text(encoding="utf-8"))
    if (report["schemaVersion"] != 1 or report["sdk"] != sdk
            or report["profileSha256"] != digest(encoded(profile))
            or report["pinnedRuntimeBuilt"] is not True or report["pinnedEmbeddingAbiVerified"] is not True
            or report["syncplayRuntimeReady"] is not False):
        raise ValueError("Runtime receipt differs from the source profile or native SDK")
    found = inventory(payload)
    found.pop("build-report.json")
    if found != report["files"]:
        raise ValueError("Runtime payload has missing, extra or changed files")
    expected = immutable_files(sources, profile)
    if sdk["target"] != "windows-x64":
        expected["configuration/" + UNIX_MAKEFILE.name] = UNIX_MAKEFILE.read_bytes()
    expected[layout(payload, sdk["target"])["stdlib"].relative_to(payload).as_posix()] = stdlib_archive(sources, profile)
    if any((payload / name).read_bytes() != data for name, data in expected.items()):
        raise ValueError("Runtime original headers, sources, stdlib or complete notices differ")
    paths = layout(payload, sdk["target"])
    native = {"runtime": paths["runtime"], "extensions": sorted(paths["extensions"].glob("*.pyd"))}
    if audit(native, sdk["target"], sdk, options) != report["nativeDependencies"]:
        raise ValueError("Runtime native dependency inventory changed")
    if relocated_probe(payload, sdk["target"]) != report["proof"]:
        raise ValueError("Runtime relocated native startup proof changed")
    return report
