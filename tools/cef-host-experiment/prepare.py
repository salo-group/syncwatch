"""Prepare a pinned vendor experiment without running its browser engine."""

import hashlib
from pathlib import Path, PurePosixPath
import posixpath
import tarfile
import tempfile
import urllib.parse


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def materialize(asset, archive, destination):
    archive, destination = Path(archive), Path(destination)
    if archive.stat().st_size != asset["compressedBytes"] or digest(archive) != asset["sha256"]:
        raise ValueError("Experimental CEF archive differs from the fixed input")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Experimental SDK destination already exists")
    name = PurePosixPath(urllib.parse.urlsplit(asset["url"]).path).name.removesuffix(".tar.bz2")
    with tarfile.open(archive, "r:bz2") as package:
        entries = package.getmembers()
        seen = set()
        expanded = 0
        for entry in entries:
            path = PurePosixPath(entry.name)
            if (not entry.name or path.is_absolute() or ".." in path.parts
                    or "\\" in entry.name or ":" in entry.name
                    or path.parts[0] != name or not (entry.isfile() or entry.isdir() or entry.issym())):
                raise ValueError("Unsafe experimental SDK archive member")
            key = path.as_posix().casefold()
            if key in seen:
                raise ValueError("Duplicate experimental SDK archive member")
            seen.add(key)
            if entry.issym():
                link = PurePosixPath(entry.linkname)
                resolved = PurePosixPath(posixpath.normpath(str(path.parent / link)))
                if (link.is_absolute() or "\\" in entry.linkname or ":" in entry.linkname
                        or not resolved.parts or resolved.parts[0] != name):
                    raise ValueError("Experimental SDK alias escapes its root")
            if entry.isfile():
                expanded += entry.size
        if expanded != asset["expandedBytes"] or not entries:
            raise ValueError("Experimental SDK expanded size differs")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".cef-prepare-", dir=destination.parent) as temporary:
            work = Path(temporary) / "payload"
            work.mkdir(mode=0o700)
            package.extractall(work, members=entries, filter="data")
            work.rename(destination)
    return destination / name


def inventory(directory):
    result = {}
    for path in sorted(Path(directory).rglob("*")):
        name = path.relative_to(directory).as_posix()
        if path.is_symlink():
            result[name] = {"link": path.readlink().as_posix()}
        elif path.is_file():
            result[name] = {"sha256": digest(path), "bytes": path.stat().st_size}
    return result


def main():
    import argparse
    import json
    import platform

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    target = {("Darwin", "arm64"): "macos-arm64", ("Linux", "x86_64"): "linux-x64",
              ("Windows", "AMD64"): "windows-x64"}.get((platform.system(), platform.machine()))
    source = Path(__file__).with_name("inputs.json")
    inputs = json.loads(source.read_text(encoding="utf-8"))
    if target not in inputs["assets"] or inputs["productionQualified"] is not False:
        raise ValueError("Unsupported or reclassified experiment input")
    asset = inputs["assets"][target]
    sdk = materialize(asset, args.archive, args.output)
    receipt = {"schemaVersion": 1, "target": target, "productionQualified": False,
               "inputsSha256": digest(source), "archiveSha256": asset["sha256"],
               "sdkDirectory": sdk.name, "files": inventory(sdk), "browserExecuted": False}
    (args.output / "preparation.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"target": target, "sdk": str(sdk), "files": len(receipt["files"]),
                      "browserExecuted": False}, sort_keys=True))


if __name__ == "__main__":
    main()
