"""Build the native helper around a previously source-built owned engine payload."""

import argparse
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/compression"))
from toolchain import resolve
from helper_sources import digest, inventory, first_party_inputs


def build(runtime, output):
    inputs = first_party_inputs()
    cmake, options, sdk = resolve()
    receipt = json.loads((runtime / "build-report.json").read_text(encoding="utf-8"))
    files = inventory(runtime)
    del files["build-report.json"]
    if (receipt["files"] != files or receipt["sourceBuiltHeadlessEngine"] is not True
            or receipt["sdk"]["native"]["target"] != sdk["target"]):
        raise ValueError("Owned source-built runtime receipt does not match")
    stock_players = ("bin/syncplay/players/", "lib/syncplay/players/",
                     "configuration/syncplay/syncplay/players/")
    if any(name.startswith(stock_players) for name in files):
        raise ValueError("Rebuild the headless Syncplay runtime with the current player selection")
    if output.exists():
        raise ValueError("Use a new helper output directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sw-helper-build-", dir=output.parent) as temporary:
        work = Path(temporary)
        owned = work / "runtime"
        shutil.copytree(runtime, owned)
        app = owned / "app"
        app.mkdir()
        for name, source in {"syncwatch_bridge": "packages/syncplay-helper/syncwatch_bridge",
                             "syncwatch_ipc": "packages/ipc/syncwatch_ipc"}.items():
            shutil.copytree(ROOT / source, app / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copytree(ROOT / "contracts", app / "contracts")
        revision = subprocess.check_output(["git", "rev-parse", "HEAD:3rdparty/syncplay"], cwd=ROOT, text=True).strip()
        (app / "syncplay-revision.txt").write_text(revision + "\n", encoding="ascii")
        # Read every validator byte from the committed submodule object, never
        # from an installed package or an uncommitted submodule worktree.
        pin = subprocess.check_output(["git", "rev-parse", "HEAD:3rdparty/fastjsonschema"], cwd=ROOT, text=True).strip()
        source = ROOT / "3rdparty/fastjsonschema"
        paths = subprocess.check_output(["git", "-C", str(source), "ls-tree", "-r", "--name-only", pin], text=True).splitlines()
        originals = {}
        for name in paths:
            if (name.startswith("fastjsonschema/") and name.endswith(".py")) or name == "LICENSE":
                data = subprocess.check_output(["git", "-C", str(source), "show", pin + ":" + name])
                relative = "app/" + name if name != "LICENSE" else "licenses/fastjsonschema/LICENSE"
                target = owned / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                originals[relative] = digest(target)
        if "licenses/fastjsonschema/LICENSE" not in originals:
            raise ValueError("Complete validator grant is missing")
        build_dir = work / "build"
        commands = [[cmake, "-S", (ROOT / "packages/syncplay-helper").as_posix(), "-B", build_dir.as_posix(),
                     *options, "-DCMAKE_BUILD_TYPE=Release", "-DSYNCWATCH_RUNTIME=" + owned.as_posix()],
                    [cmake, "--build", str(build_dir), "--config", "Release", "--parallel", "2"]]
        for command in commands:
            subprocess.run(command, cwd=ROOT, check=True)
        executable = "syncwatch-helper.exe" if platform.system() == "Windows" else "syncwatch-helper"
        binary = build_dir / ("Release/" + executable if platform.system() == "Windows" else executable)
        (owned / "bin").mkdir(exist_ok=True)
        shutil.copy2(binary, owned / "bin" / executable)
        report = {"schemaVersion": 1, "target": sdk["target"], "sdk": sdk,
                  "runtimeReceiptSha256": digest(runtime / "build-report.json"),
                  "syncplayRevision": revision, "validatorRevision": pin, "validatorOriginals": originals,
                  "sourceCommit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                  "sourceTree": subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=ROOT, text=True).strip(),
                  "localChanges": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)),
                  "firstPartyInputs": inputs,
                  "developmentHelperOnly": True, "authenticatedInstallation": False,
                  "files": inventory(owned)}
        if first_party_inputs() != inputs:
            raise ValueError("Helper build inputs changed during the build")
        (owned / "helper-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        shutil.move(str(owned), output)
    return output / "bin" / executable


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(build(args.runtime.resolve(), args.output.resolve()))


if __name__ == "__main__":
    main()
