"""Seal and revalidate the relocated headless engine and actual TLS exchange."""

import json
from pathlib import Path
import shutil
import tempfile

from source_graph import ROOT
from native_audit import audit
from source_files import digest, encoded
# Use a distinct module name for the shared interpreter packaging primitives.
import importlib.util

spec = importlib.util.spec_from_file_location("python_runtime_package", ROOT / "tools/python-build/runtime_package.py")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
layout, inventory, write_files = base.layout, base.inventory, base.write_files
from native_probe import exercise_sdk


def native_files(payload, target):
    suffixes = (".dll", ".pyd") if target == "windows-x64" else (".dylib", ".so", ".so.1.0", ".so.3")
    return sorted(p for p in payload.rglob("*") if p.is_file() and p.name.endswith(suffixes))


def relocated_probe(payload, target, logs=None):
    with tempfile.TemporaryDirectory(prefix="sw-engine-probe-") as temporary:
        owned = Path(temporary) / "runtime-\u03bc-\u0442\u0435\u0441\u0442"
        shutil.copytree(payload, owned)
        paths = layout(owned, target)
        checks = (ROOT / "tools/syncplay-runtime/runtime_checks.py").read_text(encoding="utf-8")
        abi = exercise_sdk(paths, checks)
        captured = []
        def output(name, data):
            if logs:
                logs.mkdir(parents=True, exist_ok=True)
                (logs / "engine-wire.log").write_bytes(data)
            for line in data.decode("utf-8").splitlines():
                if line.startswith("SYNCWATCH_ENGINE_PROOF "):
                    captured.append(json.loads(line.removeprefix("SYNCWATCH_ENGINE_PROOF ")))
        exercise_sdk(paths, (ROOT / "tools/syncplay-runtime/engine_fixture.py").read_text(encoding="utf-8") +
                     (ROOT / "tools/syncplay-runtime/engine_checks.py").read_text(encoding="utf-8"),
                     selected_cases=("success",), on_output=output)
        if len(captured) != 1:
            raise ValueError("Real headless TLS acceptance report is missing")
        admission = []
        def admission_output(name, data):
            if logs:
                (logs / "admission-wire.log").write_bytes(data)
            for line in data.decode("utf-8").splitlines():
                if line.startswith("SYNCWATCH_ADMISSION_PROOF "):
                    admission.append(json.loads(line.removeprefix("SYNCWATCH_ADMISSION_PROOF ")))
        exercise_sdk(paths, (ROOT / "tools/syncplay-runtime/engine_fixture.py").read_text(encoding="utf-8") +
                     (ROOT / "tools/syncplay-runtime/admission_checks.py").read_text(encoding="utf-8"),
                     selected_cases=("success",), on_output=admission_output)
        if len(admission) != 1:
            raise ValueError("Real admission TLS acceptance report is missing")
        return {"abi": abi, "engine": captured[0], "admission": admission[0]}


def proof_identity(proof):
    # Timing changes the number of legitimate canonical state messages. Every
    # consumer run asserts the lower bound and all behavioral acceptance cases.
    result = json.loads(json.dumps(proof))
    result["engine"]["playback"].pop("canonicalStateMessages")
    return result


def seal(payload, sdk, options, source_identity, commands, provenance, work):
    proof = relocated_probe(payload, sdk["native"]["target"], work / "acceptance")
    report = {"schemaVersion": 1, "sdk": sdk, "sources": source_identity,
              "commands": commands, "idnaProvenance": provenance, "files": inventory(payload),
              "nativeDependencies": audit(native_files(payload, sdk["native"]["target"]), sdk["native"]["target"], sdk["native"], options),
              "proof": proof, "sourceBuiltHeadlessEngine": True, "applicationProviderReady": False,
              "compilerStandardSupportSdkBoundary": True}
    write_files(payload, {"build-report.json": encoded(report)})
    return report


def verify(payload, sdk, options, source_identity, expected_originals, logs=None):
    report = json.loads((payload / "build-report.json").read_text(encoding="utf-8"))
    if (report["schemaVersion"] != 1 or report["sdk"] != sdk or report["sources"] != source_identity
            or report["sourceBuiltHeadlessEngine"] is not True or report["applicationProviderReady"] is not False
            or report["compilerStandardSupportSdkBoundary"] is not True):
        raise ValueError("Headless runtime receipt differs from its source or SDK identity")
    found = inventory(payload)
    found.pop("build-report.json")
    if found != report["files"]:
        raise ValueError("Headless runtime has missing, extra or changed files")
    if any((payload / name).read_bytes() != value for name, value in expected_originals.items()):
        raise ValueError("Headless runtime original source or complete grant differs")
    target = sdk["native"]["target"]
    if audit(native_files(payload, target), target, sdk["native"], options) != report["nativeDependencies"]:
        raise ValueError("Headless runtime native imports differ")
    if proof_identity(relocated_probe(payload, target, logs)) != proof_identity(report["proof"]):
        raise ValueError("Headless runtime actual consumer acceptance changed")
    return report
