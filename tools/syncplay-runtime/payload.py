"""Assemble the owned Python SDK, unchanged packages, full grants and source."""

import json
from pathlib import Path, PurePosixPath
import sys

from source_graph import ROOT, graph_data
from sources import specifications
from version_metadata import constantly_version
from sdk_legal import rust_notices

sys.path.insert(0, str(ROOT / "tools/python-build"))
from runtime_package import layout, write_files
from source_profile import notices, original_sources, stdlib_archive
from configuration import UNIX_MAKEFILE


def python_files(sources):
    graph = graph_data()
    files = {}
    for name, package in graph["pythonPackages"].items():
        prefix = "" if package["root"] == "." else package["root"] + "/"
        for path, data in sources[name].items():
            if not path.startswith(prefix):
                continue
            relative = path.removeprefix(prefix)
            if package.get("include") and not any(relative.startswith(p) for p in package["include"]):
                continue
            if any(p in {"test", "tests"} for p in relative.split("/")):
                continue
            if relative.endswith((".py", ".h", ".typed")) or relative == "certifi/cacert.pem":
                if relative in files:
                    raise ValueError("Python package namespace collision")
                files[relative] = data
        source = graph["sources"][name]
        distribution = package["distribution"]
        metadata = ("Metadata-Version: 2.4\nName: " + distribution + "\nVersion: " + source["version"]
                    + "\nLicense-Expression: " + source["license"] + "\n\n"
                    + "Metadata generated from the declared pinned original Git source.\n"
                    + source["url"] + "\n" + source["revision"] + "\n")
        directory = distribution.replace("-", "_").replace(".", "_") + "-" + source["version"] + ".dist-info/"
        files[directory + "METADATA"] = metadata.encode()
    files["constantly/_version.py"] = constantly_version(sources["constantly"], graph["sources"]["constantly"])
    return files


def originals(sources, core_sources, profile):
    files = {"include/" + name.removeprefix("Include/"): data for name, data in core_sources["cpython"].items()
             if name.startswith("Include/") and name.endswith(".h")}
    files.update(notices(core_sources, profile))
    files.update(rust_notices())
    source_files = {}
    from source_files import archive_bytes
    for name, specification in specifications().items():
        prefix = Path(specification["path"]).name
        source_files.update({prefix + "/" + path: data for path, data in sources[name].items()})
        for notice in specification["notices"]:
            files["notices/" + prefix + "/" + notice] = sources[name][notice]
    files["sources/runtime-dependencies.zip"] = archive_bytes(source_files)
    files["sources/cpython.zip"] = original_sources(core_sources, profile)
    files["LICENSE"] = (ROOT / "LICENSE").read_bytes()
    return files


def assemble(payload, target, sources, core_sources, profile, native, engine_files, core_source):
    paths = layout(payload, target)
    package_dir = paths["extensions"].relative_to(payload).as_posix() + "/"
    files = originals(sources, core_sources, profile)
    files["include/pyconfig.h"] = native["config"].read_bytes()
    files[paths["stdlib"].relative_to(payload).as_posix()] = stdlib_archive(core_sources, profile)
    files[paths["runtime"].relative_to(payload).as_posix()] = native["runtime"].read_bytes()
    files[paths["library"].relative_to(payload).as_posix()] = native["library"].read_bytes()
    files.update({package_dir + name: data for name, data in python_files(sources).items()})
    files.update({package_dir + name: data for name, data in engine_files.items()})
    for path in native["extensions"]:
        files[package_dir + path.name] = path.read_bytes()
    for path in sorted((ROOT / "packages/syncplay-engine/syncwatch_syncplay").glob("*.py")):
        files[package_dir + "syncwatch_syncplay/" + path.name] = path.read_bytes()
    for name, original in core_sources["cpython"].items():
        if (core_source / name).read_bytes() != original:
            files["configuration/cpython/" + name] = (core_source / name).read_bytes()
    if target != "windows-x64":
        for name in ["Makefile", "Modules/Setup.local"]:
            files["configuration/cpython/" + name] = (core_source.parent / "build" / name).read_bytes()
        files["configuration/cpython/" + UNIX_MAKEFILE.name] = UNIX_MAKEFILE.read_bytes()
    files["configuration/source-graph.json"] = (ROOT / "tools/syncplay-runtime/graph.json").read_bytes()
    files["configuration/python-profile.json"] = (ROOT / "tools/syncplay-runtime/python-profile.json").read_bytes()
    files["configuration/engine-profile.json"] = (ROOT / "tools/syncplay-runtime/engine-profile.json").read_bytes()
    write_files(payload, files)
    return paths
