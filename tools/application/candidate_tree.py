"""Stage immutable native application inputs without modifying SDKs or existing outputs."""

from dataclasses import dataclass
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import shutil
import stat
import tempfile


@dataclass(frozen=True)
class Component:
    source: Path
    destination: str


@contextmanager
def staging_directory(destination, platform):
    if platform == "windows-x64":
        # A private TemporaryDirectory DACL would survive publication and can
        # exclude the account's ordinary token after an elevated package build.
        # Atomically reserve a fresh directory inheriting the chosen destination.
        staging = destination / (".stage-" + secrets.token_hex(16))
        staging.mkdir()
        try:
            yield staging
        finally:
            shutil.rmtree(staging)
    else:
        with tempfile.TemporaryDirectory(prefix=".stage-", dir=destination) as temporary:
            yield Path(temporary)


def relative_path(value):
    path = PurePosixPath(value)
    if not value or "\\" in value or path.is_absolute() or any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError("Candidate component needs a contained relative destination")
    if ":" in value or "\0" in value:
        raise ValueError("Candidate destination cannot contain an alternate stream or NUL")
    return path


def inventory(root):
    """Record regular files and contained links; never traverse a directory link."""
    if getattr(Path(root).lstat(), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
        raise ValueError("Windows application components cannot contain reparse points")
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Candidate input must be a directory")
    result = {}
    for folder, directories, files in os.walk(root, followlinks=False):
        for name in sorted(directories + files):
            path = Path(folder) / name
            metadata = path.lstat()
            if getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                raise ValueError("Windows application components cannot contain reparse points")
            key = path.relative_to(root).as_posix()
            if stat.S_ISLNK(metadata.st_mode):
                target = os.readlink(path)
                if Path(target).is_absolute() or not path.resolve(strict=True).is_relative_to(root):
                    raise ValueError("Candidate link leaves its component")
                result[key] = {"kind": "link", "target": target}
            elif stat.S_ISREG(metadata.st_mode):
                with path.open("rb") as data:
                    digest = hashlib.file_digest(data, "sha256").hexdigest()
                result[key] = {"kind": "file", "bytes": metadata.st_size, "sha256": digest,
                               "executable": bool(metadata.st_mode & stat.S_IXUSR)}
            elif stat.S_ISDIR(metadata.st_mode):
                result[key] = {"kind": "directory"}
            else:
                raise ValueError("Candidate component contains a special file")
    return dict(sorted(result.items()))


def assemble(destination, components, *, platform, source_commit, source_inputs=None):
    """Reserve an owned output, validate copies, and publish one complete contents directory."""
    destination = Path(destination).absolute()
    if platform not in {"macos-arm64", "windows-x64", "linux-x64"}:
        raise ValueError("Unsupported native candidate target")
    if len(source_commit) != 40 or any(char not in "0123456789abcdef" for char in source_commit):
        raise ValueError("Candidate source commit must be an exact Git object ID")
    if not components:
        raise ValueError("Candidate requires explicit immutable components")
    sources, paths, before = [], [], []
    ordered = sorted(components, key=lambda component: (len(relative_path(component.destination).parts), component.destination))
    for component in ordered:
        source = Path(component.source)
        metadata = source.lstat()
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            raise ValueError("Candidate component root cannot be a link")
        source = source.resolve(strict=True)
        if destination.resolve().is_relative_to(source) or source.is_relative_to(destination.resolve()):
            raise ValueError("Candidate output and inputs cannot overlap")
        path = relative_path(component.destination)
        expected = inventory(source)
        if platform == "windows-x64" and any(entry["kind"] == "link" for entry in expected.values()):
            raise ValueError("Windows application components cannot contain links")
        folded = PurePosixPath(path.as_posix().casefold())
        for parent, entries in zip(paths, before, strict=True):
            parent_folded = PurePosixPath(parent.as_posix().casefold())
            if folded == parent_folded:
                raise ValueError("Candidate component destinations overlap")
            if folded.is_relative_to(parent_folded):
                if path.parts[:len(parent.parts)] != parent.parts:
                    raise ValueError("Candidate component parent casing is ambiguous")
                relative = folded.relative_to(parent_folded)
                occupied = {name.casefold(): entry for name, entry in entries.items()}
                if relative.as_posix() in occupied or any(
                    prefix.as_posix() in occupied and occupied[prefix.as_posix()]["kind"] != "directory"
                    for prefix in relative.parents if prefix != PurePosixPath(".")
                ):
                    raise ValueError("Candidate component destinations overlap")
        sources.append(source); paths.append(path); before.append(expected)
    # A previously existing output is never adopted, replaced or removed.
    destination.mkdir(parents=False, exist_ok=False)
    try:
        with staging_directory(destination, platform) as temporary:
            stage = Path(temporary) / "contents"
            stage.mkdir()
            for source, path, expected in zip(sources, paths, before, strict=True):
                target = stage / path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source, target, symlinks=True)
                if inventory(source) != expected or inventory(target) != expected:
                    raise ValueError("Candidate input changed or copied bytes differ")
            if any(inventory(source) != expected for source, expected in zip(sources, before, strict=True)):
                raise ValueError("Candidate inputs changed before publication")
            files = inventory(stage)
            report = {"schemaVersion": 1, "platform": platform, "sourceCommit": source_commit,
                      "developmentCandidate": True, "releaseReady": False,
                      "components": {str(path): data for path, data in zip(paths, before, strict=True)},
                      "files": files}
            if source_inputs is not None:
                report["sourceInputs"] = source_inputs
            # The destination was reserved by this invocation; no other builder owns it.
            receipt = Path(temporary) / "candidate.json"
            receipt.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            stage.rename(destination / "contents")
            receipt.rename(destination / "candidate.json")
        return report
    except BaseException:
        # Only this invocation's newly reserved tree may be removed.
        shutil.rmtree(destination)
        raise
