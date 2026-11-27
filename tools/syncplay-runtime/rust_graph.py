"""Bind Cargo's actual enabled packages to the exact audited Git authorities."""

import json
from pathlib import Path, PurePosixPath
import subprocess
import tomllib

from source_graph import graph_data


WORKSPACE = "syncwatch-cryptography-build"
VERSION = "0.1.0"
CONFIGURATION = "configuration/cryptography/"


def build_recipe():
    graph = graph_data()

    def original(source, path):
        name = PurePosixPath(graph["sources"][source]["path"]).name
        return json.dumps(PurePosixPath("../originals", name, path).as_posix())

    manifest = (
        f'[package]\nname = "{WORKSPACE}"\nversion = "{VERSION}"\nedition = "2021"\npublish = false\n'
        '[workspace]\nresolver = "2"\n'
        '[dependencies]\ncryptography-rust = { path = ' + original("cryptography", "src/rust") + ' }\n'
        '[profile.release]\noverflow-checks = true\n')
    overrides = "[patch.crates-io]\n" + "".join(
        json.dumps(crate["name"]) + " = { path = " + original(crate["source"], crate["path"]) + " }\n"
        for crate in graph["crates"])
    return {"Cargo.toml": manifest.encode(), ".cargo/config.toml": overrides.encode(), "src/lib.rs": b""}


def configure(work):
    # Select the upstream cdylib as a path dependency. Cargo resolves the
    # selected package into our lockfile without building dependency tests.
    root = work / "rust-build"
    root.mkdir()
    for name, value in build_recipe().items():
        path = root / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(value)
    return root


def packaged_configuration(work):
    root = work / "rust-build"
    return {CONFIGURATION + name: (root / name).read_bytes() for name in (*build_recipe(), "Cargo.lock")}


def verify_configuration(payload, sources, commands):
    root = payload / CONFIGURATION
    for name, expected in build_recipe().items():
        file = root / name
        if not file.is_file() or file.read_bytes() != expected:
            raise ValueError("Headless runtime Cargo workspace differs from the pinned build recipe")
    lock = root / "Cargo.lock"
    if not lock.is_file():
        raise ValueError("Headless runtime Cargo lockfile is missing")
    data = tomllib.loads(lock.read_text(encoding="utf-8"))
    packages = data.get("package", [])
    if data.get("version") != 4 or any("source" in package or "checksum" in package for package in packages):
        raise ValueError("Headless runtime Cargo lockfile contains nonlocal crate metadata")
    authority = {crate["name"]: {"source": crate["source"], "version": crate["version"]}
                 for crate in graph_data()["crates"]}
    expected = {name: info["version"] for name, info in authority.items()} | {WORKSPACE: VERSION}
    workspace = tomllib.loads(sources["Cargo.toml"].decode())["workspace"]
    for member in workspace["members"]:
        package = tomllib.loads(sources[member.rstrip("/") + "/Cargo.toml"].decode())["package"]
        version = package["version"]
        expected[package["name"]] = workspace["package"]["version"] if version == {"workspace": True} else version
    found = {package.get("name"): package.get("version") for package in packages}
    if len(packages) != len(found) or found != expected or commands.get("crateAuthorities") != authority:
        raise ValueError("Headless runtime Cargo crate identities differ from the pinned graph")


def verify_metadata(cargo, root, paths, environment, logs):
    try:
        with (logs / "metadata.log").open("wb") as errors:
            result = subprocess.run([str(cargo), "metadata", "--offline", "--format-version", "1"],
                cwd=root, env=environment, stdout=subprocess.PIPE, stderr=errors, check=True, timeout=120)
    except (subprocess.SubprocessError, OSError):
        raise ValueError("Native build step Cargo metadata failed. See the local metadata.log.") from None
    data = json.loads(result.stdout)
    graph = graph_data()
    expected = {c["name"]: (c["version"], (paths[c["source"]] / c["path"] / "Cargo.toml").resolve())
                for c in graph["crates"]}
    root_manifest = (root / "Cargo.toml").resolve()
    cryptography = paths["cryptography"].resolve()
    found = {}
    for package in data["packages"]:
        path = Path(package["manifest_path"]).resolve()
        if package["source"] is not None:
            raise ValueError("Cargo resolved an external registry or Git dependency")
        if path == root_manifest and package["name"] == WORKSPACE:
            continue
        if path.is_relative_to(cryptography):
            if not package["name"].startswith("cryptography-"):
                raise ValueError("Unknown cryptography workspace package")
            continue
        identity = package["version"], path
        if package["name"] not in expected or identity != expected[package["name"]]:
            raise ValueError("Cargo selected an undeclared original source: " + package["name"])
        found[package["name"]] = identity
    if found != expected:
        raise ValueError("Cargo omitted a declared enabled crate authority")
    return {name: {"version": version, "source": next(c["source"] for c in graph["crates"] if c["name"] == name)}
            for name, (version, _path) in sorted(found.items())}
