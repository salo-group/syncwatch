"""Select and verify the original GNU libiconv release inputs for Windows."""

import json

from source_profile import ROOT, materialize
from source_files import digest


GENERATED_HEADERS = {"lib/aliases_dos.h", "lib/canonical.h", "lib/canonical_dos.h", "lib/canonical_local.h"}
GENERATOR_INPUTS = (
    "genaliases.c", "genaliases2.c", "encodings.def", "encodings_local.def", "encodings_dos.def",
)


def generate_headers(context, upstream, aliases):
    work = context.work / "iconv-generators"
    work.mkdir()
    for name in GENERATOR_INPUTS:
        (work / name).write_bytes(upstream["lib/" + name])
    # The release's canonical scripts read the string table already produced by Gperf.
    (work / "tmp.h").write_bytes(aliases)
    compiler = context.env["CC"]
    commands = {
        "compile-aliases": [compiler, "genaliases.c", "-o", "genaliases.exe"],
        "generate-aliases": ["./genaliases.exe", "aliases.gperf", "canonical.sh", "canonical_local.sh"],
        "generate-canonical": ["sh", "-c", "sh canonical.sh > canonical.h && sh canonical_local.sh > canonical_local.h"],
        "compile-dos": [compiler, "-DUSE_DOS", "genaliases2.c", "-o", "genaliases2.exe"],
        "generate-dos": ["./genaliases2.exe", "dos", "aliases_dos.h", "canonical_dos.h"],
    }
    for label, arguments in commands.items():
        context.run("iconv-" + label, arguments, cwd=work, shell=True, timeout=30)
    # Windows text output uses CRLF. Select LF before checking the GNU release digests.
    return {name: (work / name.removeprefix("lib/")).read_bytes().replace(b"\r\n", b"\n")
            for name in GENERATED_HEADERS}


def materialize_release(context, destination, mirror, upstream, profile):
    expected = json.loads((ROOT / "tools/vlc/iconv-original.json").read_bytes())["files"]
    files = {name: mirror["source/" + name] for name in expected.keys() - GENERATED_HEADERS - {
        "lib/iconv.c", "lib/aliases.h",
    }}
    files["lib/iconv.c"] = upstream["lib/iconv.c"]
    files["lib/aliases.h"] = mirror["source/lib/aliases.h.orig"]
    if any(digest(data) != expected[name] for name, data in files.items()):
        raise ValueError("libiconv build inputs differ from the original GNU release")
    generated = generate_headers(context, upstream, files["lib/aliases.h"])
    if any(digest(data) != expected[name] for name, data in generated.items()):
        raise ValueError("libiconv generated headers differ from the original GNU release")
    executables = [name.removeprefix("source/") for name in profile["sources"]["libiconv"]["executables"]]
    materialize(destination, files | generated, {"executables": executables}, profile["sourceDateEpoch"])
