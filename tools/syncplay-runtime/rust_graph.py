"""Bind Cargo's actual enabled packages to the exact audited Git authorities."""

import json
import os
from pathlib import Path
import subprocess

from source_graph import graph_data


WORKSPACE = "syncwatch-cryptography-build"


def configure(work, paths):
    # Select the upstream cdylib as a path dependency. Cargo resolves the
    # selected package into our lockfile without building dependency tests.
    root = work / "rust-build"
    (root / "src").mkdir(parents=True)
    (root / ".cargo").mkdir()
    (root / "src/lib.rs").write_bytes(b"")

    def relative(path):
        return json.dumps(Path(os.path.relpath(path, root)).as_posix())

    (root / "Cargo.toml").write_text(
        f'[package]\nname = "{WORKSPACE}"\nversion = "0.1.0"\nedition = "2021"\npublish = false\n'
        '[dependencies]\ncryptography-rust = { path = ' + relative(paths["cryptography"] / "src/rust") + ' }\n'
        '[profile.release]\noverflow-checks = true\n', encoding="utf-8", newline="\n")
    overrides = "[patch.crates-io]\n" + "".join(
        json.dumps(crate["name"]) + " = { path = " + relative(paths[crate["source"]] / crate["path"]) + " }\n"
        for crate in graph_data()["crates"])
    (root / ".cargo/config.toml").write_text(overrides, encoding="utf-8", newline="\n")
    return root


def verify_metadata(cargo, root, paths, environment):
    data = json.loads(subprocess.check_output([str(cargo), "metadata", "--offline", "--format-version", "1"],
        cwd=root, env=environment, stderr=subprocess.PIPE, timeout=120))
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
