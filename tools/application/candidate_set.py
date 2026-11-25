"""Verify the three development archives for one reviewed native CI run."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from zipfile import BadZipFile, ZipFile

from candidate_tree import matches_ci_receipt, relative_path


TARGETS = {
    "macos": ("arm64", {
        "SyncWatch.app/Contents/MacOS/SyncWatch": True,
        "SyncWatch.app/Contents/Info.plist": False,
    }),
    "windows": ("x64", {
        "SyncWatch/SyncWatch.exe": False,
        "SyncWatch/SyncWatch.dll": False,
        "SyncWatch/SyncWatch.deps.json": False,
        "SyncWatch/SyncWatch.runtimeconfig.json": False,
    }),
    "linux": ("x64", {
        "SyncWatch/SyncWatch": True,
        "SyncWatch/launch.args": False,
        "SyncWatch/Runtime/bin/java": True,
    }),
}
ARCHIVE_LIMIT = json.loads((Path(__file__).resolve().parents[1] / "ci/artifact-policy.json").read_bytes())[
    "nativeApplicationMaximumBytes"
]
# Use the native CI SDK consumer's existing expansion limits.
EXPANDED_LIMIT = 3 * 1024**3
ENTRY_LIMIT = 100000
METADATA_LIMIT = 8 * 1024 * 1024
RECEIPT = "candidate/candidate.json"
MANIFEST = "build-manifest.json"
CONTENTS = "candidate/contents/"


class VerificationError(ValueError):
    """A safe diagnostic for an invalid candidate input."""


def require(condition, message):
    if not condition:
        raise VerificationError(message)


def require_object(value):
    require(isinstance(value, dict), "Expected a JSON object")
    return value


def decode(data):
    def unique_keys(pairs):
        result = dict(pairs)
        require(len(result) == len(pairs), "Duplicate JSON member")
        return result

    return require_object(json.loads(data, object_pairs_hook=unique_keys))


def matches(value, pattern):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def read_metadata(package, name):
    require(package.getinfo(name).file_size <= METADATA_LIMIT, "Archive metadata exceeds its bound")
    return package.read(name)


def verify_members(package, entries, manifest, receipt, launch_inputs):
    files = require_object(manifest["files"])
    require("validation.xml" in files and all(name != MANIFEST and not name.startswith("candidate/") for name in files),
            "Native report inventory is incomplete or overlaps the candidate")
    expected = {name: (digest, None, None) for name, digest in files.items()}
    expected[RECEIPT] = (manifest["candidateReceiptSha256"], None, None)
    tree = require_object(receipt["files"])
    for name, execute in launch_inputs.items():
        require(name in tree, "Candidate launch input is missing")
        launch_file = require_object(tree[name])
        require(launch_file.get("kind") == "file" and type(launch_file.get("bytes")) is int and launch_file["bytes"] > 0,
                "Candidate launch input is missing or empty")
        require(not execute or launch_file.get("executable") is True, "Candidate launcher is not executable")
    directories = set()
    for name, record in tree.items():
        path = relative_path(name)
        if path.parent != PurePosixPath("."):
            parent = require_object(tree.get(path.parent.as_posix()))
            require(parent.get("kind") == "directory", "Candidate inventory parent is not a directory")
        record = require_object(record)
        if record["kind"] == "directory":
            directories.add(CONTENTS + name + "/")
        else:
            size, executable = record["bytes"], record["executable"]
            require(record["kind"] == "file" and type(size) is int and size >= 0 and type(executable) is bool,
                    "Candidate inventory must declare regular files and directories")
            expected[CONTENTS + name] = (record["sha256"], size, executable)
    components = require_object(receipt["components"])
    require(bool(components), "Candidate component inventory is missing")
    for root, inventory in components.items():
        component = require_object(tree.get(root))
        require(component.get("kind") == "directory", "Candidate component root is missing")
        for name, record in require_object(inventory).items():
            require(tree.get(root + "/" + name) == record, "Candidate component inventory differs")
    names = {entry.orig_filename for entry in entries}
    require(len(names) == len(entries) == len({name.casefold() for name in names})
            and names == {MANIFEST, *expected, *directories},
            "Archive members differ from the declared inventory")
    for entry in entries:
        name = entry.orig_filename
        path = relative_path(name.removesuffix("/"))
        require(not re.search(r'[<>"|*?\x00-\x1f]', name)
                and not any(part.endswith((".", " ")) or re.fullmatch(
                    r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part, re.I) for part in path.parts),
                "Archive member path is not portable")
        mode = entry.external_attr >> 16
        if name in directories:
            require(stat.S_ISDIR(mode) and entry.file_size == 0, "Invalid candidate directory entry")
            continue
        require(stat.S_IFMT(mode) in (0, stat.S_IFREG), "Archive contains a non-regular file")
        if name == MANIFEST:
            continue
        digest, size, executable = expected[name]
        require(matches(digest, r"[0-9a-f]{64}"), "Invalid inventory digest")
        if size is not None:
            require(entry.file_size == size and bool(mode & stat.S_IXUSR) == executable, "Candidate file metadata differs")
        with package.open(entry) as data:
            require(hashlib.file_digest(data, "sha256").hexdigest() == digest, "Archive member digest differs")
    return len(tree)


def verify_archive(platform, archive, request, directory):
    architecture, launch_inputs = TARGETS[platform]
    digest = archive["sha256"]
    require(matches(digest, r"[0-9a-f]{64}"), "Expected an independently recorded SHA-256")
    path = directory / archive["path"]
    require(path.is_file() and not path.is_symlink(), "Archive must be a regular file")
    with path.open("rb") as data:
        before = os.fstat(data.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_size <= ARCHIVE_LIMIT, "Archive exceeds its transport bound")
        require(hashlib.file_digest(data, "sha256").hexdigest() == digest, "Archive SHA-256 differs")
        with ZipFile(data) as package:
            entries = package.infolist()
            require(len(entries) <= ENTRY_LIMIT and sum(entry.file_size for entry in entries) <= EXPANDED_LIMIT,
                    "Archive contents exceed their expansion bounds")
            manifest = decode(read_metadata(package, MANIFEST))
            require(all(manifest.get(key) == request[key] for key in ("sourceCommit", "sourceHead", "sourceTree", "runId"))
                    and manifest.get("platform") == platform and manifest.get("architecture") == architecture
                    and matches(manifest.get("runAttempt"), r"[1-9][0-9]*") and manifest.get("schemaVersion") == 1
                    and manifest.get("validation") == "success" and manifest.get("applicationBuilt") is True
                    and manifest.get("validationScope") == "native-build-and-standalone-package",
                    "Native archive has a different source, run, target, or validation result")
            receipt = decode(read_metadata(package, RECEIPT))
            require(matches_ci_receipt(receipt, f"{platform}-{architecture}", request["sourceCommit"]),
                    "Candidate receipt differs from the reviewed source")
            count = verify_members(package, entries, manifest, receipt, launch_inputs)
        after = os.fstat(data.fileno())
        require((before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                (after.st_size, after.st_mtime_ns, after.st_ctime_ns), "Archive changed during verification")
    return {"sha256": digest, "runAttempt": manifest["runAttempt"],
            "candidateReceiptSha256": manifest["candidateReceiptSha256"], "inventoryEntries": count}


def verify(request_path):
    require(request_path.is_file(), "Expected a regular verification request")
    with request_path.open("rb") as data:
        request_bytes = data.read(METADATA_LIMIT + 1)
    require(len(request_bytes) <= METADATA_LIMIT, "Verification request exceeds its bound")
    request = decode(request_bytes)
    source = {key: request[key] for key in ("sourceCommit", "sourceHead", "sourceTree")}
    require(all(matches(value, r"[0-9a-f]{40}") for value in source.values()), "Expected exact Git object IDs")
    require(matches(request.get("runId"), r"[1-9][0-9]*"), "Expected a native CI run ID")
    archives = require_object(request["archives"])
    require(set(archives) == set(TARGETS), "Expected one archive each for macOS, Windows, and Linux")
    verified = {}
    for platform in TARGETS:
        try:
            verified[platform] = verify_archive(platform, require_object(archives[platform]), request, request_path.parent)
        except VerificationError as error:
            raise VerificationError(f"{platform} archive rejected. {error}") from error
        except (BadZipFile, KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
            raise VerificationError(f"{platform} archive is unreadable or has invalid metadata") from error
    return {"schemaVersion": 1, **source, "runId": request["runId"], "archives": verified, "releaseReady": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path)
    args = parser.parse_args()
    try:
        evidence = verify(args.request)
    except VerificationError as error:
        print(f"Candidate set rejected. {error}", file=sys.stderr)
        return 1
    except (KeyError, OSError, TypeError, ValueError):
        print("Candidate set rejected. The verification request is unreadable or invalid.", file=sys.stderr)
        return 1
    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
