"""Reuse Linux runtime compilation in CI while verifying current inputs and receipts."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

from sdk_cache import ROOT, cold_requested, inside, sha256


def modules():
    sys.path.insert(0, str(ROOT / "tools/dependencies"))
    sys.path.insert(0, str(ROOT / "tools/application"))
    import build
    import receipts
    return build, receipts


def snapshot(context):
    dependencies, receipts = modules()
    receipts.require_host("linux-x64")
    media = Path(context["media"])
    receipts.media(media, "linux-x64")
    sys.path.insert(0, str(ROOT / "tools/jvm"))
    import jdk
    jdk.home()  # Verify the complete pinned boot/compiler SDK on cache hits too.
    prefixes = ["tools/application", "tools/ci", "tools/jvm", "tools/native", "tools/vlc", ".gitmodules"]
    tools = {}
    for name in ("gcc", "g++", "make", "ld", "ar", "objcopy", "strip", "patchelf", "pkg-config"):
        executable = Path(shutil.which(name)).resolve(strict=True)
        tools[name] = sha256(executable)
    auxiliary = {}
    for field in ("autotools", "xorg_macros", "gnu_config", "pkg_config_macros"):
        directory = Path(context[field])
        auxiliary[field] = {p.relative_to(directory).as_posix(): sha256(p)
                            for p in sorted(directory.rglob("*")) if p.is_file()}
    sources = {}
    for field in ("jvm_sources", "client_sources"):
        directory = Path(context[field])
        sources[field] = {p.name: sha256(p) for p in sorted(directory.iterdir()) if p.is_file()}
    return {
        "schemaVersion": 1, "purpose": "ci-linux-source-built-runtimes",
        "files": dependencies.input_files(ROOT, {"inputs": prefixes}),
        "sources": sources, "tools": tools, "auxiliary": auxiliary,
        "bootJdk": json.loads((ROOT / "tools/jvm/toolchains.json").read_text()),
        "mediaReceiptSha256": sha256(media / "build-report.json"),
        "host": {"system": platform.system(), "architecture": platform.machine(), "release": platform.release(),
                 "image": os.environ.get("ImageOS"), "imageVersion": os.environ.get("ImageVersion")},
        "environment": {name: os.environ.get(name) for name in (
            "CC", "CXX", "CPP", "AR", "AS", "LD", "NM", "OBJCOPY", "OBJDUMP", "RANLIB", "STRIP",
            "CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS", "CONFIG_SITE", "MAKEFLAGS", "LC_ALL", "LANG")},
        "jobs": 2,
    }


def request_path(directory):
    return inside(ROOT, Path(directory).relative_to(ROOT) / "runtime-cache-request.json")


def prepare(directory, context):
    dependencies, _ = modules()
    inputs = snapshot(context)
    key = hashlib.sha256(dependencies.canonical(inputs)).hexdigest()
    request = {"key": key, "inputs": inputs, "context": context, "directory": str(directory),
               "archive": str(inside(ROOT, f".local/ci-linux-runtime-cache/{key}.zip")),
               "cold": cold_requested()}
    request_path(directory).write_text(json.dumps(request, indent=2) + "\n")
    return request


def read_request(path):
    path = inside(ROOT, Path(path).relative_to(ROOT))
    request = json.loads(path.read_text(encoding="utf-8"))
    dependencies, _ = modules()
    current = snapshot(request["context"])
    if (dependencies.canonical(current) != dependencies.canonical(request["inputs"])
            or hashlib.sha256(dependencies.canonical(current)).hexdigest() != request["key"]):
        raise ValueError("Linux runtime compilation inputs changed during CI")
    return request


def verify(payload, request):
    _, receipts = modules()
    for name, check in (("jvm", receipts.jvm), ("clients", receipts.clients)):
        report = check(payload / name)
        if report.get("mediaReceiptSha256") != request["inputs"]["mediaReceiptSha256"]:
            raise ValueError("Cached Linux runtime uses a different verified media SDK")


def smoke(payload, request):
    ambient = {"JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "JDK_JAVAC_OPTIONS", "_JAVA_OPTIONS", "CLASSPATH",
               "CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS", "LIBRARY_PATH", "CPATH", "LD_PRELOAD"}
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("ACTIONS_", "INPUT_")) and key != "GITHUB_TOKEN" and key not in ambient}
    environment["LD_LIBRARY_PATH"] = os.pathsep.join((str(payload / "jvm/lib"),
                                                     str(Path(request["context"]["media"]) / "lib")))
    subprocess.run([str(payload / "jvm/bin/java"), "--list-modules"], cwd=payload / "jvm",
                   env=environment, stdout=sys.stderr, check=True, timeout=180)


def unpack_verified(archive, staging, request):
    dependencies, _ = modules()
    manifest = dependencies.unpack(archive, staging, request)
    verify(staging / "payload", request)
    smoke(staging / "payload", request)
    return manifest


def restore(path):
    request = read_request(path)
    archive = inside(ROOT, Path(request["archive"]).relative_to(ROOT))
    if request["cold"] or not archive.exists():
        return False
    directory = inside(ROOT, Path(request["directory"]).relative_to(ROOT))
    with tempfile.TemporaryDirectory(prefix=".runtime-cache-", dir=directory) as temporary:
        staging = Path(temporary)
        manifest = unpack_verified(archive, staging, request)
        for name in ("jvm", "clients"):
            destination = directory / name
            if destination.exists() or destination.is_symlink():
                raise ValueError("Linux runtime cache cannot replace an existing build output")
        for name in ("jvm", "clients"):
            destination = directory / name
            destination.mkdir()
            (staging / "payload" / name).rename(destination / "payload")
        request["buildSourceCommit"] = manifest["buildSourceCommit"]
        request["cacheHit"] = True
        Path(path).write_text(json.dumps(request, indent=2) + "\n")
    return True


def validate_staged(path):
    request = read_request(path)
    archive = inside(ROOT, Path(request["archive"]).relative_to(ROOT))
    if not archive.exists():
        return  # The fresh runtime can exceed the optional cache transport bound.
    directory = inside(ROOT, Path(request["directory"]).relative_to(ROOT))
    with tempfile.TemporaryDirectory(prefix=".runtime-cache-check-", dir=directory) as temporary:
        unpack_verified(archive, Path(temporary), request)
    print("Fresh Linux runtime archive passed cache extraction, receipt and JVM execution checks", file=sys.stderr)


def stage(path):
    request = read_request(path)
    dependencies, _ = modules()
    directory = inside(ROOT, Path(request["directory"]).relative_to(ROOT))
    archive = inside(ROOT, Path(request["archive"]).relative_to(ROOT))
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".runtime-cache-", dir=directory) as temporary:
        staging = Path(temporary)
        for name in ("jvm", "clients"):
            shutil.copytree(directory / name / "payload", staging / "payload" / name, symlinks=True)
        verify(staging / "payload", request)
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        manifest = {"schemaVersion": 1, "key": request["key"], "inputs": request["inputs"], "result": "success",
                    "buildSourceCommit": request.get("buildSourceCommit", commit), "verifiedSourceCommit": commit}
        try:
            packed = dependencies.pack(staging, manifest)
        except ValueError as error:
            if str(error) != "Built archive exceeds its upload bound":
                raise
            archive.unlink(missing_ok=True)
            print("Linux runtime cache exceeds its transport bound; retaining the fresh build", file=sys.stderr)
            return
        packed.replace(archive)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "restore", "stage", "validate-staged"))
    parser.add_argument("path", type=Path)
    for name in ("media", "autotools", "xorg-macros", "gnu-config", "pkg-config-macros", "jvm-sources", "client-sources"):
        parser.add_argument("--" + name)
    args = parser.parse_args()
    if args.operation == "prepare":
        context = {name: getattr(args, name) for name in (
            "media", "autotools", "xorg_macros", "gnu_config", "pkg_config_macros", "jvm_sources", "client_sources")}
        if not all(context.values()):
            parser.error("Runtime cache preparation requires the complete current build context")
        prepare(args.path, context)
    elif args.operation == "restore":
        print(json.dumps({"hit": restore(args.path)}))
    elif args.operation == "validate-staged":
        validate_staged(args.path)
    else:
        stage(args.path)


if __name__ == "__main__":
    main()
