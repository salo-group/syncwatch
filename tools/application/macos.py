"""Assemble the native Swift application with its default bundle runtime locations."""

import plistlib
import shutil
import subprocess

from candidate_tree import Component, inventory
from receipts import ROOT, require_host


def prepare(arguments, work):
    require_host("macos-arm64")
    binary = arguments.application.resolve(strict=True)
    architectures = subprocess.check_output(["lipo", "-archs", str(binary)], text=True).split()
    if "arm64" not in architectures:
        raise ValueError("Native macOS application must contain arm64 code")
    info = plistlib.loads((ROOT / "apps/macos/Resources/Info.plist").read_bytes())
    if info.get("CFBundleExecutable") != "SyncWatch" or info.get("CFBundleIdentifier") != "group.salo.syncwatch":
        raise ValueError("Application bundle identity differs from its native entry")
    bundle = work / "SyncWatch.app"
    contents = bundle / "Contents"
    executable = contents / "MacOS/SyncWatch"
    resources = contents / "Resources"
    executable.parent.mkdir(parents=True)
    resources.mkdir()
    shutil.copy2(binary, executable)
    (contents / "Info.plist").write_bytes(plistlib.dumps(info))
    shutil.copy2(ROOT / "LICENSE", resources / "LICENSE")

    # These runtime trees include original sources and notices. Seal them as
    # resources so codesign does not interpret the data as nested code.
    runtimes = [
        (arguments.media_package, resources / "Media"),
        (arguments.helper_package, resources / "Syncplay"),
    ]
    expected = [inventory(source) for source, _ in runtimes]
    for source, destination in runtimes:
        shutil.copytree(source, destination, symlinks=True)

    # The native linker signs the bare executable. Seal the completed app
    # without forcing new signatures onto the pinned runtime components.
    subprocess.run(["codesign", "--force", "--sign", "-", "--timestamp=none", str(bundle)], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(bundle)], check=True)
    for (source, destination), original in zip(runtimes, expected, strict=True):
        if inventory(source) != original or inventory(destination) != original:
            raise ValueError("macOS bundle sealing changed a pinned runtime")
    return [Component(bundle, "SyncWatch.app")]
