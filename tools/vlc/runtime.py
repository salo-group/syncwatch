"""Build or freshly revalidate the owned libVLC SDK on its native host."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from build_context import BuildContext, native_target
import libraries
from native_audit import audit
from native_package import assemble, relocate
from native_probe import environment, verify as media_verify
import package_report
from source_package import expected, write
from source_profile import ROOT, materialize, profile_data, snapshots
from source_files import digest, read_snapshot
import upstream


def codec_check(context, package):
    output = subprocess.check_output([sys.executable, str(ROOT / "tools/vlc/ffmpeg_checks.py"),
        str(package), "8.1.2"], env=environment(package, context.target), timeout=60)
    return json.loads(output)


def playback_check(context, package):
    # Consumer source is verified freshly against this checkout, separately from
    # the SDK payload/cache identity. The native compiler SDK is already active.
    spec = importlib.util.spec_from_file_location("syncwatch_playback_sdk_check", ROOT / "tools/playback/sdk_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.verify(package, context.work / "first-party-playback")


def source_identity(profile):
    return {name: {"revision": source["revision"], "files": source["files"]}
            for name, source in profile["sources"].items()}


def run(operation, directory, payload, original, profile):
    work = directory / ("native-build" if operation == "build" else "native-verify")
    if work.exists():
        if work.is_symlink():
            raise ValueError("Native build work must not be a symbolic link")
        shutil.rmtree(work)
    context = BuildContext(work, profile["sourceDateEpoch"])
    sdk = context.sdk()
    sources = source_identity(profile)
    contents = expected(original, profile, context)
    if operation == "build":
        trees = {}
        for name, specification in profile["sources"].items():
            trees[name] = work / "sources" / name
            materialize(trees[name], original[name], specification, profile["sourceDateEpoch"])
        libraries.build(context, trees, profile)
        build = upstream.build(context, trees, profile)
        package = work / "package"
        assemble(context, build, trees["vlc"], profile, package)
        relocate(context, package)
        write(package, contents)
        write(package, {"build/commands.json": (work / "commands.json").read_bytes(),
                        "build/configuration-derivatives.json": (work / "configuration-derivatives.json").read_bytes()})
        imports = audit(context, package)
        proof = media_verify(context, package)
        codec = codec_check(context, package)
        package_report.create(package, sdk, sources, imports, proof, codec)
        package_report.verify(package, sdk, sources, contents)
        if any(payload.iterdir()):
            raise ValueError("Native media build requires an empty owned payload")
        shutil.copytree(package, payload, dirs_exist_ok=True)
    else:
        retained = package_report.verify(payload, sdk, sources, contents)
        imports = audit(context, payload)
        codec = codec_check(context, payload)
        if imports != retained["imports"] or codec != retained["codec"]:
            raise ValueError("Actual native imports or codec APIs differ from their build report")
        proof = media_verify(context, payload)
        # Frame counters and measured PCM frequency vary with scheduling; the
        # native consumer enforces behavior and bounds in every fresh process.
        if (set(proof["media"]) != set(retained["proof"]["media"]) or
                set(proof["effects"]) != set(retained["proof"]["effects"])):
            raise ValueError("Cached media fixture coverage changed")
    consumer = None
    if operation == "verify":
        consumer = playback_check(context, payload)
        package_report.verify(payload, sdk, sources, contents)
    return {"component": "libvlc", "target": context.target, "profile": profile["profile"],
            "sources": len(original), "nativeFiles": len(imports), "fixtures": sorted(proof["media"]),
            "effects": sorted(proof["effects"]),
            "seekReset": proof["seekReset"],
            "physicalOutputAccepted": False, "applicationIntegrationReady": False,
            "firstPartyPlayback": consumer,
            "sourceInventorySha256": digest(contents["sources/inventory.json"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["build", "verify", "sdk"])
    parser.add_argument("--platform", choices=["macos-arm64", "windows-x64", "linux-x64"])
    args = parser.parse_args()
    target = native_target()
    if args.platform and args.platform != target:
        raise ValueError("Media SDKs must be built and verified on their actual native target")
    profile = profile_data()
    directory = ROOT / "artifacts/dependencies/libvlc" / target
    if args.operation == "sdk":
        print(json.dumps(BuildContext(directory / "sdk-check", profile["sourceDateEpoch"]).sdk(), sort_keys=True))
        return
    if args.operation == "build":
        if json.loads(os.environ["SYNCWATCH_DEPENDENCY_FLAGS"]) != {"profile": profile["profile"], "version": profile["version"]}:
            raise ValueError("Unknown native media build profile")
        original = snapshots(Path(os.environ["SYNCWATCH_DEPENDENCY_SOURCES"]), profile)
        payload = Path(os.environ["SYNCWATCH_DEPENDENCY_OUTPUT"])
    else:
        original = {name: read_snapshot(directory / (name + ".tar"), source)
                    for name, source in profile["sources"].items()}
        payload = directory / "payload"
    print(json.dumps(run(args.operation, directory, payload, original, profile), sort_keys=True))


if __name__ == "__main__":
    main()
