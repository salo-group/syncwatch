"""Cache pinned developer archives for CI; never cache installed tools or test results."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2]
MAXIMUM_BYTES = 256 * 1024**2
PLATFORMS = ("linux-x64", "macos-arm64", "windows-x64")


def cold_requested():
    if os.environ.get("CI_COLD") == "true" or os.environ.get("GITHUB_EVENT_NAME") == "schedule":
        return True
    path = os.environ.get("GITHUB_EVENT_PATH")
    event = json.loads(Path(path).read_text(encoding="utf-8")) if path else {}
    return event.get("inputs", {}).get("cold") in (True, "true")


def inside(root, name):
    path = root / name
    if not path.resolve().is_relative_to(root.resolve()) or any(
            p.is_symlink() for p in (path, *path.parents) if p.is_relative_to(root)):
        raise ValueError("SDK cache path must stay inside its workspace without symbolic links")
    return path


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def plan(root, platform, sdks):
    if platform not in PLATFORMS or not sdks or len(sdks) != len(set(sdks)):
        raise ValueError("Invalid SDK cache selection")
    groups, commands = [], {}
    for sdk in sdks:
        if sdk == "jdk":
            item = json.loads((root / "tools/jvm/toolchains.json").read_text())["targets"][platform]
            directory = f".local/ci-sdk-inputs/jdk/{platform}"
            entries = [("vendor.archive", item)]
            commands[sdk] = ["tools/jvm/jdk.py", "--provision", "--archive", directory + "/vendor.archive"]
        elif sdk == "kotlin":
            spec = json.loads((root / "tools/jvm/kotlin_toolchain.json").read_text())
            directory = ".local/ci-sdk-inputs/kotlin"
            entries = [("vendor.zip", spec["archive"]), ("vendor.spdx.json", spec["sbom"])]
            commands[sdk] = ["tools/jvm/kotlin.py", "--provision", "--archive", directory + "/vendor.zip",
                             "--sbom", directory + "/vendor.spdx.json"]
        elif sdk == "windows-vlc" and platform == "windows-x64":
            spec = json.loads((root / "tools/vlc/windows-sdk.json").read_text())
            directory = ".local/vlc-sdk/windows"
            entries = [("base.tar.xz", spec["base"]), *sorted(spec["archives"].items())]
            commands[sdk] = ["tools/vlc/provision_sdks.py"]
        else:
            raise ValueError("Unsupported SDK cache selection")
        records = []
        for name, item in entries:
            url = urllib.parse.urlsplit(item["url"])
            if (not re.fullmatch(r"[A-Za-z0-9_.+-]+", name) or name in {".", ".."}
                    or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
                    or url.scheme != "https" or not url.hostname or url.username or url.password):
                raise ValueError("SDK archives require safe names, HTTPS and exact digests")
            prefix = "/packages" if sdk == "windows-vlc" and name != "base.tar.xz" else ""
            destination = directory + prefix + "/" + name
            inside(root, destination)
            records.append({"name": name, "url": item["url"], "sha256": item["sha256"], "destination": destination})
        # Bound each transport artifact without recompressing vendor archives.
        for offset in range(0, len(records), 8):
            members = records[offset:offset + 8]
            identity = {"schema": 1, "purpose": "ci-developer-sdk-archives", "sdk": sdk, "entries": members}
            key = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            groups.append({"key": key, "sdk": sdk, "entries": members,
                           "archive": f".local/ci-sdk-cache/{key}.zip"})
    return {"groups": groups, "commands": commands, "cold": cold_requested()}


def restore_group(root, group):
    archive = inside(root, group["archive"])
    expected = {entry["name"]: entry for entry in group["entries"]}
    with zipfile.ZipFile(archive) as package, tempfile.TemporaryDirectory(dir=archive.parent) as temporary:
        entries = package.infolist()
        if (archive.stat().st_size > MAXIMUM_BYTES or len(entries) != len(expected)
                or {entry.filename for entry in entries} != set(expected)
                or sum(entry.file_size for entry in entries) > MAXIMUM_BYTES):
            raise ValueError("SDK cache differs from its exact vendor archive inventory")
        for entry in entries:
            if entry.external_attr >> 16 & 0o170000 not in (0, 0o100000):
                raise ValueError("SDK cache must contain only regular vendor archives")
            staged = Path(temporary) / entry.filename
            with package.open(entry) as source, staged.open("xb") as destination:
                shutil.copyfileobj(source, destination)
            if sha256(staged) != expected[entry.filename]["sha256"]:
                raise ValueError("Cached vendor archive checksum mismatch")
        # No restored bytes are published until every member passes its pin.
        for name, entry in expected.items():
            destination = inside(root, entry["destination"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            (Path(temporary) / name).replace(destination)


def download_entry(root, entry, cold):
    destination = inside(root, entry["destination"])
    if destination.exists() and not cold:
        if not destination.is_file() or sha256(destination) != entry["sha256"]:
            raise ValueError("Local vendor archive checksum mismatch")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as output:
            temporary = Path(output.name)
            with urllib.request.urlopen(entry["url"], timeout=120) as source:
                digest, size = hashlib.sha256(), 0
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAXIMUM_BYTES:
                        raise ValueError("Vendor archive exceeds its download bound")
                    digest.update(chunk)
                    output.write(chunk)
        if digest.hexdigest() != entry["sha256"]:
            raise ValueError("Downloaded vendor archive checksum mismatch")
        temporary.replace(inside(root, entry["destination"]))
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def pack_group(root, group):
    archive = inside(root, group["archive"])
    files = [(entry["name"], inside(root, entry["destination"])) for entry in group["entries"]]
    # An oversized group still builds from verified downloads, without caching.
    if sum(path.stat().st_size for _, path in files) + 65536 > MAXIMUM_BYTES:
        archive.unlink(missing_ok=True)
        return
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=archive.parent) as temporary:
        staged = Path(temporary) / "archive.zip"
        with zipfile.ZipFile(staged, "w", compression=zipfile.ZIP_STORED) as package:
            for name, path in files:
                package.write(path, name)
        staged.replace(archive)


def materialize(root, selection, cold=False):
    for group in selection["groups"]:
        archive = inside(root, group["archive"])
        if archive.exists() and not cold:
            restore_group(root, group)
    entries = [entry for group in selection["groups"] for entry in group["entries"]]
    with ThreadPoolExecutor(max_workers=4) as workers:
        list(workers.map(lambda entry: download_entry(root, entry, cold), entries))
    for group in selection["groups"]:
        pack_group(root, group)


def sdk_command(sdk, platform, root=ROOT):
    # CI transports archives separately; existing provisioners verify and install.
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return [sys.executable, *plan(root, platform, [sdk])["commands"][sdk]]
    return [sys.executable, f"tools/jvm/{sdk}.py", "--provision"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("plan", "materialize"))
    parser.add_argument("--platform", required=True, choices=PLATFORMS)
    parser.add_argument("--sdk", required=True, action="append")
    parser.add_argument("--cold", action="store_true")
    args = parser.parse_args()
    selection = plan(ROOT, args.platform, args.sdk)
    if args.operation == "plan":
        print(json.dumps(selection))
    else:
        materialize(ROOT, selection, cold=args.cold or selection["cold"])
        print("Developer SDK archives match their pinned checksums")


if __name__ == "__main__":
    main()
