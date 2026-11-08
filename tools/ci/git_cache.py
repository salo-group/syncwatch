"""Reuse pinned Git objects in CI without copying Git configuration or hooks."""

import argparse
import configparser
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/dependencies"))
import build
from sdk_cache import cold_requested


@contextmanager
def offline_git():
    values = {**build.OFFLINE, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
              "GIT_CONFIG_COUNT": "0"}
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def git(root, *arguments):
    return subprocess.check_output(["git", *arguments], cwd=root, stderr=subprocess.PIPE, timeout=180)


def selection(root, component, platform, cold=False):
    registered = build.registry(root)
    build.require(component in registered and registered[component]["status"] == "ready", "Invalid Git cache component")
    specification = registered[component]
    build.require(platform in specification["targets"], "Invalid Git cache platform")
    config = configparser.ConfigParser(interpolation=None)
    config.read(root / ".gitmodules", encoding="utf-8")
    urls = {config[section]["path"]: config[section]["url"] for section in config.sections()}
    sources = {path: {"url": urls[path], "specification": source}
               for path, source in sorted(specification["sources"].items()) if build.export_paths(source) is not None}
    inputs = {"schemaVersion": 1, "purpose": "ci-pinned-git-objects", "component": component, "sources": sources,
              "implementation": {"cache": build.digest(Path(__file__)),
                                 "exports": build.digest(root / "tools/dependencies/build.py")}}
    key = hashlib.sha256(build.canonical(inputs)).hexdigest()
    archive = build.generated_path(root, f".local/ci-git-cache/{key}.zip")
    return {"key": key, "inputs": inputs, "archive": archive.relative_to(root).as_posix(),
            "eligible": bool(sources), "cold": cold or cold_requested(),
            "publisher": platform == ("linux-x64" if "linux-x64" in specification["targets"] else min(specification["targets"]))}


def object_name(name):
    return bool(re.fullmatch(r"[0-9a-f]{2}/[0-9a-f]{38}", name)
                or re.fullmatch(r"pack/pack-[0-9a-f]{40}\.(pack|idx|rev|promisor)", name))


def materialize(directory, checkout_root, source_path, specification, url):
    source = build.generated_path(checkout_root, source_path)
    source.mkdir(parents=True)
    git(source.parent, "-c", "init.templateDir=", "init", "-q", str(source))
    count = 0
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            name = path.relative_to(directory).as_posix()
            build.require(object_name(name) and not path.is_symlink(), "Git cache contains a non-object member")
            target = build.generated_path(source, ".git/objects/" + name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            count += 1
    build.require(count > 0, "Git cache has no object data")
    for name, value in (("core.repositoryformatversion", "1"), ("extensions.partialClone", "origin"),
                        ("remote.origin.url", url), ("remote.origin.promisor", "true"),
                        ("remote.origin.partialclonefilter", "blob:none")):
        git(source, "config", "--local", name, value)
    inventory = git(source, "cat-file", "--batch-all-objects", "--batch-check=%(objectname) %(objecttype)")
    build.require(len(inventory) <= 16 * 1024**2, "Git object inventory exceeds its bound")
    commits = []
    for line in inventory.decode("ascii").splitlines():
        identity, kind = line.split()
        build.require(re.fullmatch(r"[0-9a-f]{40}", identity), "Invalid cached Git object identity")
        if kind == "commit":
            commits.append(identity)
    revision = specification["revision"]
    build.require(revision in commits, "Git cache omitted its pinned commit")
    # A source snapshot does not import history or references from the producer.
    # Treat each retained commit as a boundary while verifying every object hash.
    (source / ".git/shallow").write_text("".join(commit + "\n" for commit in commits), encoding="ascii")
    git(source, "update-ref", "--no-deref", "HEAD", revision)
    git(source, "fsck", "--full", "--no-reflogs", "--no-dangling")
    build.initialize_export_source(checkout_root, source_path, specification, url)
    build.require(git(source, "rev-parse", "HEAD").decode().strip() == revision, "Git cache changed its source pin")
    build.require(not git(source, "status", "--porcelain", "--untracked-files=normal").strip(),
                  "Git cache checkout is not clean")


def check_archive(root, selected, *, install):
    archive = build.generated_path(root, selected["archive"])
    parent = archive.parent
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="objects-", dir=parent) as temporary:
        work = Path(temporary)
        build.unpack(archive, work, selected)
        expected = set()
        pending = []
        with offline_git():
            for index, (path, source) in enumerate(selected["inputs"]["sources"].items()):
                directory = work / "payload" / str(index)
                build.require(directory.is_dir(), "Git cache omitted a source object database")
                expected.add(str(index))
                destination = build.generated_path(root, path)
                if install and (destination / ".git").exists():
                    continue
                build.require(not install or not destination.exists() or not any(destination.iterdir()),
                              "Git cache cannot replace an existing source checkout")
                sandbox = work / "checkouts" / path
                materialize(directory, work / "checkouts", path, source["specification"], source["url"])
                pending.append((sandbox, destination))
        build.require({p.name for p in (work / "payload").iterdir()} == expected,
                      "Git cache contains unexpected source databases")
        if install:
            for sandbox, destination in pending:
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    destination.rmdir()
                sandbox.rename(destination)
        return len(pending)


def restore(root, selected):
    archive = build.generated_path(root, selected["archive"])
    if selected["cold"] or not selected["eligible"] or not archive.exists():
        return {"hit": False, "restored": 0}
    return {"hit": True, "restored": check_archive(root, selected, install=True)}


def stage(root, selected):
    if not selected["eligible"]:
        return
    archive = build.generated_path(root, selected["archive"])
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="publish-", dir=archive.parent) as temporary:
        work = Path(temporary)
        for index, (path, source) in enumerate(selected["inputs"]["sources"].items()):
            checkout = build.inside(root, path)
            build.require(git(checkout, "rev-parse", "HEAD").decode().strip() == source["specification"]["revision"],
                          "Git cache publisher has a different source pin")
            build.require(not git(checkout, "status", "--porcelain", "--untracked-files=normal").strip(),
                          "Git cache publisher has changed source files")
            objects = Path(git(checkout, "rev-parse", "--git-path", "objects").decode().strip())
            if not objects.is_absolute():
                objects = checkout / objects
            objects = objects.resolve(strict=True)
            for path in sorted(objects.rglob("*")):
                if path.is_file() and object_name(path.relative_to(objects).as_posix()):
                    build.require(not path.is_symlink(), "Git object publisher cannot follow symbolic links")
                    target = work / "payload" / str(index) / path.relative_to(objects)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, target)
        manifest = {"schemaVersion": 1, "key": selected["key"], "inputs": selected["inputs"], "result": "success"}
        try:
            packed = build.pack(work, manifest)
        except ValueError as error:
            if str(error) not in {"Built archive exceeds its upload bound", "Built payload exceeds its bound"}:
                raise
            archive.unlink(missing_ok=True)
            print("Git object cache exceeds its optional transport bound; retaining the fresh source build", file=sys.stderr)
            return
        packed.replace(archive)
    check_archive(root, selected, install=False)
    print("Fresh Git object cache passed offline fsck and exact pinned checkout checks", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("plan", "restore", "stage"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--component", required=True)
    parser.add_argument("--platform", required=True, choices=build.PLATFORMS)
    parser.add_argument("--cold", action="store_true")
    args = parser.parse_args()
    try:
        root = args.root.resolve()
        selected = selection(root, args.component, args.platform, args.cold)
        if args.operation == "plan":
            print(json.dumps(selected))
        elif args.operation == "restore":
            print(json.dumps(restore(root, selected)))
        else:
            stage(root, selected)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, zipfile.BadZipFile):
        print("Git source cache failed; inspect the pinned object and source validation", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
