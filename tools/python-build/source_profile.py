"""Pinned interpreter inputs, complete notices and original-source packaging."""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/dependencies"))
from source_files import archive_bytes, digest, encoded, read_snapshot

COMPONENT = "cpython-embedded"


def profile_data():
    value = json.loads((ROOT / "tools/python-build/profile.json").read_text(encoding="utf-8"))
    if value["schemaVersion"] != 1 or value["syncplayRuntimeReady"] is not False or value["version"] != "3.12.15":
        raise ValueError("Unsupported embedded interpreter profile")
    for source in [value["source"], value["zlib"], value["unicode"]]:
        if (source["exports"] != sorted(set(source["exports"]))
                or not set(source["notices"]) <= set(source["files"])
                or not set(source["executables"]) <= set(source["files"])
                or any(not any(name == p or name.startswith(p + "/") for p in source["exports"]) for name in source["files"])):
            raise ValueError("Interpreter source or notice inventory differs")
    component = json.loads((ROOT / "tools/dependencies/components.json").read_text())["components"][COMPONENT]
    expected = {source["path"]: {**{k: source[k] for k in ("revision", "license", "notices", "exports")},
                                "requires": [], "unusedSubmodules": {}}
                for source in [value["source"], value["zlib"], value["unicode"]]}
    if component["sources"] != expected or set(component["roots"]) != set(expected):
        raise ValueError("Interpreter source/license graph differs from the registry")
    unicode = json.loads((ROOT / "tools/python-unicode/profile.json").read_text())
    if (value["unicode"]["files"] != unicode["sources"]["unicode"]["files"]
            or value["source"]["files"]["Objects/unicodetype_db.h"] != unicode["sources"]["cpython"]["files"]["Objects/unicodetype_db.h"]):
        raise ValueError("Interpreter Unicode table/data differs from the reviewed source profile")
    return value


def read_sources(paths, profile):
    return {name: read_snapshot(paths[name], profile[key]) for name, key in [("cpython", "source"), ("zlib", "zlib"), ("unicode", "unicode")]}


def materialize(root, files, executables=()):
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o755 if name in executables else 0o644)
        # Original generated headers and inputs share a fixed source timestamp;
        # building must not accidentally regenerate them with an ambient SDK.
        import os
        os.utime(path, (1_700_000_000, 1_700_000_000))


def validate(sources, profile):
    for name, key in [("cpython", "source"), ("zlib", "zlib"), ("unicode", "unicode")]:
        if {p: digest(data) for p, data in sources[name].items()} != profile[key]["files"]:
            raise ValueError("Interpreter original source or full notice differs")
    required = {
        "Python/dtoa.c": [b"Copyright (c) 1991, 2000, 2001 by Lucent Technologies.", b"provided that this entire notice", b"AS IS"],
        "Python/getopt.c": [b"Copyright 1992-1994, David Gottner", b"following disclaimer notice appear unmodified"],
        "Python/dynamic_annotations.c": [b"Copyright (c) 2008-2009, Google Inc.", b"THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS"],
        "Include/dynamic_annotations.h": [b"Copyright (c) 2008-2009, Google Inc.", b"Neither the name of Google Inc."],
        "Modules/_sre/sre.c": [b"Copyright (c) 1997-2001 by Secret Labs AB.", b"Python 1.6 license"],
        "Python/hashtable.c": [b"Copyright (c) 2005 Don Owens", b"THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS"],
        "Python/pyhash.c": [b"<MIT License>", b"Copyright (c) 2013", b"</MIT License>"],
    }
    for name, markers in required.items():
        if not all(marker in sources["cpython"][name] for marker in markers):
            raise ValueError("Embedded interpreter origin or complete grant is missing: " + name)


def stdlib_archive(sources, profile):
    return archive_bytes({name.removeprefix("Lib/"): sources["cpython"][name] for name in profile["stdlib"]})


def original_sources(sources, profile):
    # This source distribution includes the compiled core and its headers,
    # configured-module originals, original generators and selected stdlib.
    cpython = sources["cpython"]
    selected = {name: data for name, data in cpython.items()
                if name.startswith(("Include/", "Python/", "Objects/", "Parser/", "PC/", "PCbuild/", "Tools/build/"))}
    selected.update({name: cpython[name] for name in profile["stdlib"]})
    # Frozen Python code is compiled into the library as well as C sources.
    # Preserve every original input declared by the pinned freeze project.
    import xml.etree.ElementTree as ET
    namespace = {"m": "http://schemas.microsoft.com/developer/msbuild/2003"}
    project = ET.fromstring(cpython["PCbuild/_freeze_module.vcxproj"])
    for item in project.findall(".//m:None", namespace):
        if item.find("m:ModName", namespace) is not None:
            path = item.get("Include").replace("\\", "/").removeprefix("../")
            selected[path] = cpython[path]
    # Parse declarations from the pinned original inputs.
    import re
    bootstrap = cpython["Modules/Setup.bootstrap.in"].decode()
    modules = set(re.findall(r"[a-zA-Z0-9_/]+\.c", "\n".join(l for l in bootstrap.splitlines() if not l.startswith("#"))))
    modules.update(Path(p).relative_to("Modules").as_posix() for values in profile["extensions"].values() for p in values)
    modules.update(["gcmodule.c", "getbuildinfo.c", "getpath.c", "getpath.py", "getpath_noop.c", "main.c", "zlibmodule.c", "_io/winconsoleio.c"])
    for name, data in cpython.items():
        if (name in profile["source"]["notices"] or name in [
                "configure", "Makefile.pre.in", "pyconfig.h.in", "Modules/makesetup", "Modules/Setup",
                "Modules/Setup.bootstrap.in", "Modules/Setup.stdlib.in", "Modules/config.c.in", "Tools/freeze/makeconfig.py"]
                or name.startswith("Programs/") or name.startswith(("Modules/_sre/", "Modules/_io/"))
                or name in ["Modules/" + item for item in modules]):
            selected[name] = data
    # Follow only the selected sources' quoted headers. Unused optional vendor
    # directories are build inputs, not part of this runtime distribution.
    pending = list(selected)
    while pending:
        name = pending.pop()
        if Path(name).suffix not in {".c", ".h"}:
            continue
        for header in re.findall(rb'^\s*#\s*include\s*"([^"]+)"', selected[name], re.M):
            relative = header.decode("ascii")
            candidates = [(Path(name).parent / relative).as_posix(), "Modules/" + relative, "Include/" + relative]
            for path in candidates:
                if path in cpython and path not in selected:
                    selected[path] = cpython[path]; pending.append(path)
    return archive_bytes({"cpython/" + name: data for name, data in selected.items()} |
                         {"zlib/" + name: data for name, data in sources["zlib"].items()} |
                         {"unicode/" + name: data for name, data in sources["unicode"].items()})


def notices(sources, profile):
    result = {}
    for name, key in [("cpython", "source"), ("zlib", "zlib"), ("unicode", "unicode")]:
        for path in profile[key]["notices"]:
            result["notices/" + Path(profile[key]["path"]).name + "/" + path] = sources[name][path]
    return result
