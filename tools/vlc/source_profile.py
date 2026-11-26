"""Verify original pinned media sources and materialize isolated build inputs."""

import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/dependencies"))
from source_files import digest, read_snapshot, member


def output_libraries(profile, target):
    helpers = profile.get("platformHelperLibraries", {}).get(target, [])
    private = profile.get("platformPrivateLibraries", {}).get(target, [])
    for names in (helpers, private):
        if names != sorted(set(names)):
            raise ValueError("Native output libraries require a unique sorted inventory")
        for name in names:
            member(name)
    if any(Path(name).name != name for name in helpers) or any(not name.startswith("lib/") for name in private):
        raise ValueError("Native output library leaves its source-owned directory")
    plugins = set(selected_plugins(profile, target))
    if target == "linux-x64" and plugins & {"pulse", "xcb_x11", "xcb_window"}:
        if (not {"pulse", "xcb_x11", "xcb_window"} <= plugins
                or set(helpers) != {"libvlc_pulse.so.0", "libvlc_xcb_events.so.0"}
                or len(private) != 1 or not Path(private[0]).name.startswith("libpulsecommon-")
                or not {"pulseaudio", "libsndfile", "desktop-libxcb", "desktop-xorgproto"} <= profile["sources"].keys()):
            raise ValueError("Native Linux output source/loader closure is incomplete")
    return helpers, private


def profile_data():
    profile = json.loads((ROOT / "tools/vlc/profile.json").read_text())
    if profile["schemaVersion"] != 1 or profile["profile"] != "native-decoder-v1":
        raise ValueError("Unknown native media profile")
    for source in profile["sources"].values():
        if (source["exports"] != sorted(set(source["exports"])) or
                not set(source["notices"]) <= set(source["files"]) or
                not set(source["executables"]) <= set(source["files"])):
            raise ValueError("Media source/grant inventory is incomplete")
    for target in ("macos-arm64", "windows-x64", "linux-x64"):
        output_libraries(profile, target)
    component = json.loads((ROOT / "tools/dependencies/components.json").read_bytes())["components"]["libvlc"]
    expected = {source["path"]: {key: source[key] for key in
                ["revision", "license", "notices", "exports", "requires", "unusedSubmodules", "checkoutDirectories"] if key in source}
                for source in profile["sources"].values()}
    if component.get("status") != "ready" or component.get("sources") != expected:
        raise ValueError("Registered media source graph differs from the reviewed build profile")
    return profile


def snapshots(directory, profile):
    return {name: read_snapshot(directory / source["path"], source)
            for name, source in profile["sources"].items()}


def selected_plugins(profile, target):
    return sorted(set(profile["plugins"] + profile.get("platformPlugins", {}).get(target, [])))


def materialize(directory, files, source, epoch):
    for name, data in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o755 if name in source["executables"] else 0o644)
        os.utime(path, (epoch, epoch))
