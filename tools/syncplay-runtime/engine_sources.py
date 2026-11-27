"""Retain original engine inputs and record the narrowly configured derivatives."""

import json
from pathlib import Path

from source_graph import ROOT
from engine_configuration import configure, defaults
from source_files import encoded, read_snapshot, archive_bytes
from source_profile import materialize


def specifications():
    return json.loads(Path(__file__).with_name("engine-profile.json").read_text(encoding="utf-8"))["sources"]


def read_sources(paths):
    return {name: read_snapshot(paths[name], spec) for name, spec in specifications().items()}


def assemble(sources, work):
    source = work / "syncplay"
    materialize(source, sources["syncplay"])
    configure(source)
    generated = defaults(source)
    files = {name: data for name, data in sources["syncplay"].items()
             if name.startswith("syncplay/") and name.endswith(".py")
             and not name.startswith(("syncplay/ui/", "syncplay/players/"))}
    client = "syncplay/client.py"
    files[client] = (source / client).read_bytes()
    changed = {"configuration/syncplay/" + client: files[client]}
    files["syncwatch_syncplay/defaults.json"] = encoded(generated)
    return files, changed


def originals(sources):
    files, archive = {}, {}
    for name, spec in specifications().items():
        prefix = Path(spec["path"]).name
        archive.update({prefix + "/" + n: b for n, b in sources[name].items()})
        files.update({"notices/" + prefix + "/" + n: sources[name][n] for n in spec["notices"]})
    files["sources/syncplay.zip"] = archive_bytes(archive)
    return files
