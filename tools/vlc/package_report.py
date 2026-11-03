"""Bind a native payload to exact sources and reject changed or extra members."""

import json

from source_files import digest, encoded

REPORT = "build-report.json"


def validate_seek_reset(result):
    if (not isinstance(result, dict) or result.get("pausedSeeks") != 30
            or any(result.get(key) is not True for key in
                   ["success", "actualClock", "normalClose", "ownedLibraryLoaded", "temporaryFixturesRemoved"])
            or result.get("physicalOutputAccepted") is not False
            or type(result.get("decodedBefore")) is not int or result["decodedBefore"] < 0
            or type(result.get("decodedAfter")) is not int
            or result["decodedAfter"] < result["decodedBefore"] + 4):
        raise ValueError("Native decoder seek reset proof requires fresh video, native clock and normal close")


def inventory(package):
    files = {}
    for path in sorted(package.rglob("*")):
        if path.is_symlink():
            raise ValueError("Native media packages require regular files")
        if path.is_file():
            name = path.relative_to(package).as_posix()
            if name != REPORT:
                files[name] = digest(path.read_bytes())
    if not files:
        raise ValueError("Native media package is empty")
    return files


def create(package, sdk, sources, imports, proof, codec):
    validate_seek_reset(proof.get("seekReset"))
    report = {"schemaVersion": 1, "profile": "native-decoder-v1", "sdk": sdk,
              "sources": sources, "imports": imports, "proof": proof, "codec": codec,
              "physicalOutputAccepted": False, "applicationIntegrationReady": False,
              "hardwareH264BackwardSeekAccepted": False, "files": inventory(package)}
    (package / REPORT).write_bytes(encoded(report))
    return report


def verify(package, sdk, sources, original_contents):
    path = package / REPORT
    if not path.is_file() or path.is_symlink():
        raise ValueError("Missing regular native media build report")
    report = json.loads(path.read_bytes())
    if (report.get("schemaVersion") != 1 or report.get("profile") != "native-decoder-v1" or
            report.get("sdk") != sdk or report.get("sources") != sources or
            any(report.get(key) is not False for key in ["physicalOutputAccepted", "applicationIntegrationReady",
                                                       "hardwareH264BackwardSeekAccepted"]) or
            report.get("files") != inventory(package)):
        raise ValueError("Native media payload, source or SDK identity changed")
    validate_seek_reset(report.get("proof", {}).get("seekReset"))
    for name, data in original_contents.items():
        if not (package / name).is_file() or (package / name).read_bytes() != data:
            raise ValueError("Original media source, recipe or grant changed: " + name)
    return report
