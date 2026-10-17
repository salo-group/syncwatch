"""Run a native acceptance command and prepare a bounded, commit-identified CI artifact."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET
import zipfile


ROOT = Path(__file__).resolve().parents[2]


def sha256(path: Path) -> str:
    with path.open("rb") as data:
        return hashlib.file_digest(data, "sha256").hexdigest()


class Report:
    def __init__(self):
        self.cases = []

    def execute(self, name, operation):
        started = time.monotonic()
        case = {"name": name, "success": False}
        self.cases.append(case)
        try:
            result = operation()
            case["success"] = True
            return result
        finally:
            case["seconds"] = time.monotonic() - started

    def write(self, path: Path) -> None:
        suite = ET.Element("testsuite", {
            "name": "Native validation", "tests": str(len(self.cases)),
            "failures": str(sum(not case["success"] for case in self.cases)),
        })
        for case in self.cases:
            item = ET.SubElement(suite, "testcase", {"name": case["name"], "time": f"{case['seconds']:.3f}"})
            if not case["success"]:
                ET.SubElement(item, "failure", {"message": "The validation stage did not succeed"})
        ET.indent(suite)
        path.write_bytes(ET.tostring(suite, encoding="utf-8", xml_declaration=True) + b"\n")


def check_host(target: str) -> str:
    expected = {"apple": ("Darwin", {"arm64"}), "windows": ("Windows", {"AMD64", "x86_64"})}
    system, architectures = expected[target]
    if platform.system() != system or platform.machine() not in architectures:
        raise ValueError("Native runner OS or architecture does not match the declared lane")
    return "arm64" if target == "apple" else "x64"


def extract_sdk(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as package:
        entries = package.infolist()
        if len(entries) > 100000 or sum(item.file_size for item in entries) > 3 * 1024**3:
            raise ValueError("SDK archive exceeds extraction bounds")
        for entry in entries:
            member = PurePosixPath(entry.filename)
            if member.is_absolute() or ".." in member.parts or "\\" in entry.filename or ":" in entry.filename:
                raise ValueError("SDK archive contains an unsafe path")
            if entry.external_attr >> 16 & 0o170000 == 0o120000:
                raise ValueError("SDK archive contains a symbolic link")
            if not (destination / entry.filename).resolve().is_relative_to(destination.resolve()):
                raise ValueError("SDK archive leaves its destination")
        package.extractall(destination)


def ensure_dotnet(specification: dict, temporary: Path, root: Path = ROOT) -> Path:
    installed = shutil.which("dotnet")
    if installed:
        version = subprocess.run([installed, "--version"], cwd=root, capture_output=True, text=True)
        if version.returncode == 0 and version.stdout.strip() == specification["dotnetSdk"]:
            return Path(installed)
    destination = temporary / f"syncwatch-dotnet-{specification['dotnetSdk']}"
    destination.mkdir(parents=True, exist_ok=True)
    archive = temporary / "syncwatch-dotnet-sdk.zip"
    url = urlsplit(specification["sdkArchive"])
    if url.scheme != "https" or url.hostname != "builds.dotnet.microsoft.com" or url.username or url.password:
        raise ValueError("SDK provisioning requires the declared official HTTPS source")
    print(f"Provisioning .NET SDK {specification['dotnetSdk']} from its verified archive", flush=True)
    digest = hashlib.sha512()
    total = 0
    with urllib.request.urlopen(specification["sdkArchive"], timeout=60) as response, archive.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > 1024**3:
                raise ValueError("SDK download exceeds its bound")
            digest.update(chunk)
            output.write(chunk)
    if digest.hexdigest() != specification["sdkArchiveSha512"]:
        raise ValueError("SDK archive checksum does not match its source manifest")
    extract_sdk(archive, destination)
    archive.unlink()
    executable = destination / "dotnet.exe"
    version = subprocess.check_output([str(executable), "--version"], cwd=root, text=True).strip()
    if version != specification["dotnetSdk"]:
        raise ValueError("Provisioned SDK does not match the exact pin")
    return executable


def apple_candidate(root: Path, output: Path) -> None:
    bundle = root / "artifacts/apple/SyncWatch.app"
    if not (bundle / "Contents/MacOS/SyncWatch").is_file():
        raise ValueError("Native application bundle was not built")
    with tarfile.open(output, "w") as archive:
        for source in [bundle, *sorted(bundle.rglob("*"))]:
            if source.is_symlink():
                raise ValueError("Development bundle must not contain symbolic links")
            member = archive.gettarinfo(str(source), str(source.relative_to(bundle.parent)))
            member.uid = member.gid = 0
            member.uname = member.gname = ""
            member.mtime = 0
            if source.is_file():
                with source.open("rb") as data:
                    archive.addfile(member, data)
            else:
                archive.addfile(member)


def prepare_archive(root: Path, directory: Path, target: str, architecture: str, versions: dict, report: Report, success: bool) -> Path:
    payload = directory / "payload"
    payload.mkdir(parents=True, exist_ok=True)
    report.write(payload / "validation.xml")
    if success:
        if target == "apple":
            apple_candidate(root, payload / "SyncWatch.app.tar")
        else:
            source = root / "apps/windows/SyncWatch/bin/Debug/net10.0-windows"
            candidate = payload / "SyncWatch"
            candidate.mkdir()
            for name in ("SyncWatch.exe", "SyncWatch.dll", "SyncWatch.Domain.dll", "SyncWatch.deps.json", "SyncWatch.runtimeconfig.json"):
                shutil.copy2(source / name, candidate / name)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    manifest = {
        "schemaVersion": 1, "sourceCommit": commit,
        "sourceHead": os.environ.get("CI_HEAD_COMMIT", commit),
        "sourceTree": subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=root, text=True).strip(),
        "platform": target, "architecture": architecture, "validation": "success" if success else "failure",
        "runId": os.environ.get("GITHUB_RUN_ID"), "runAttempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "runner": {key: os.environ.get(key) for key in ("ImageOS", "ImageVersion", "RUNNER_OS", "RUNNER_ARCH")},
        "toolchains": versions,
        "files": {str(path.relative_to(payload)).replace("\\", "/"): sha256(path) for path in sorted(payload.rglob("*")) if path.is_file()},
    }
    (payload / "build-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    output = directory / "artifact.zip"
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(payload.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(payload))
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=("apple", "windows"))
    args = parser.parse_args()
    specification = json.loads((ROOT / "tools/native/toolchains.json").read_text())[args.target]
    directory = ROOT / "artifacts/native" / args.target
    if directory.is_symlink() or not directory.resolve().is_relative_to(ROOT):
        raise ValueError("Native report directory must remain in the workspace")
    if directory.exists():
        shutil.rmtree(directory)
    (directory / "payload").mkdir(parents=True)
    report, versions = Report(), {}
    architecture, success = "unverified", False
    environment = os.environ.copy()
    try:
        architecture = report.execute("Verify runner architecture", lambda: check_host(args.target))
        if args.target == "apple":
            def select_toolchain():
                if environment.get("GITHUB_ACTIONS") == "true":
                    environment["DEVELOPER_DIR"] = f"/Applications/Xcode_{specification['ciXcodeVersion']}.app/Contents/Developer"
                for tool, command in (("swift", ["swift", "--version"]), ("xcode", ["xcodebuild", "-version"])):
                    versions[tool] = subprocess.check_output(command, cwd=ROOT, env=environment, text=True).strip()
                if environment.get("GITHUB_ACTIONS") == "true" and versions["xcode"].splitlines()[0] != f"Xcode {specification['ciXcodeVersion']}":
                    raise ValueError("Selected Xcode differs from the native CI pin")
            report.execute("Select Apple toolchain", select_toolchain)
            command = [sys.executable, "tools/native/check_apple.py"]
        else:
            temporary = Path(os.environ["RUNNER_TEMP"])
            dotnet = report.execute("Provision exact .NET SDK", lambda: ensure_dotnet(specification, temporary))
            versions["dotnet"] = subprocess.check_output([str(dotnet), "--version"], cwd=ROOT, text=True).strip()
            command = ["pwsh", "-NoProfile", "-File", "tools/native/check_windows.ps1", "-DotNet", str(dotnet)]
        report.execute("Native formatting, domain tests, and application build", lambda: subprocess.run(command, cwd=ROOT, env=environment, check=True))
        report.execute("Verify unchanged tracked source", lambda: subprocess.run(["git", "diff", "--exit-code"], cwd=ROOT, check=True))
        success = True
    except Exception:
        print(f"Native {args.target} validation failed; consult the failing stage in the job log", file=sys.stderr)
    finally:
        prepare_archive(ROOT, directory, args.target, architecture, versions, report, success)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
