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
import tempfile
import time
import urllib.request
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET
import zipfile

from sdk_cache import cold_requested, inside, sdk_command


ROOT = Path(__file__).resolve().parents[2]
WINDOWS_SERVICE_STAGE = "Authenticated room HTTP, Syncplay admission and owned host lifecycle"
NATIVE_ARCHIVE_BOUND = json.loads((ROOT / "tools/ci/artifact-policy.json").read_text())["nativeApplicationMaximumBytes"]


def sha256(path: Path) -> str:
    with path.open("rb") as data:
        return hashlib.file_digest(data, "sha256").hexdigest()


def seed_dependency_cache(root, archive, request):
    with zipfile.ZipFile(archive) as package:
        entry = package.getinfo("build-manifest.json")
        if entry.file_size > 1024**2:
            raise ValueError("Dependency manifest exceeds its bound")
        manifest = json.loads(package.read(entry))
    canonical = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    if (manifest.get("result") != "success" or manifest.get("key") != request["key"]
            or canonical(manifest.get("inputs")) != canonical(request["inputs"])):
        print("Current-run dependency inputs differ; rebuilding with this runner", flush=True)
        return False
    relative = Path(request["cacheFile"]).relative_to(root)
    if relative.parts[:2] != (".local", "dependency-cache"):
        raise ValueError("Dependency cache must remain in its declared directory")
    cache = inside(root, relative)
    cache.parent.mkdir(parents=True, exist_ok=True)
    # The build entry point still unpacks, validates all bytes, runs verifyCommand
    # and emits a fresh manifest for this consumer. No test result is reused.
    with tempfile.TemporaryDirectory(dir=cache.parent) as temporary:
        staged = Path(temporary) / "cache.zip"
        shutil.copyfile(archive, staged)
        staged.replace(cache)
    return True


def restore_current_dependency_cache(component, target, root=ROOT):
    if os.environ.get("GITHUB_ACTIONS") != "true" or cold_requested():
        return False
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("ACTIONS_", "INPUT_")) and key != "GITHUB_TOKEN"}
    subprocess.run([sys.executable, "tools/dependencies/build.py", "prepare", "--component", component,
                    "--platform", target], cwd=root, env=environment, check=True)
    directory = root / "artifacts/dependencies" / component / target
    request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(dir=directory) as temporary:
        archive = Path(temporary) / "dependency.zip"
        subprocess.run(["node", "tools/application/fetch_input.mjs",
                        f"syncwatch-dependency-{component}-{target}", str(archive)], cwd=root, check=True)
        return seed_dependency_cache(root, archive, request)


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
    expected = {"macos": ("Darwin", {"arm64"}), "windows": ("Windows", {"AMD64", "x86_64"}),
                "linux": ("Linux", {"x86_64"})}
    system, architectures = expected[target]
    if platform.system() != system or platform.machine() not in architectures:
        raise ValueError("Native runner OS or architecture does not match the declared lane")
    return "arm64" if target == "macos" else "x64"


def extract_sdk(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as package:
        entries = package.infolist()
        if len(entries) > 100000 or sum(item.file_size for item in entries) > 3 * 1024**3:
            raise ValueError("SDK archive exceeds extraction bounds")
        for entry in entries:
            # ZipInfo normalizes Windows separators before exposing filename.
            original = entry.orig_filename
            member = PurePosixPath(original)
            if member.is_absolute() or ".." in member.parts or any(c in original for c in ("\\", ":", "\x00")):
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


def design_image_names(root: Path) -> set[str]:
    contract = json.loads((root / "packages/design/components.json").read_text(encoding="utf-8"))['components']
    layout = json.loads((root / "packages/design/tokens/geometry.json").read_text(encoding="utf-8"))['layout']
    size = lambda w, h: str(layout[w]['value']) + "x" + str(layout[h]['value'])
    sizes = [size("galleryWidth", "galleryHeight"), size("minimumWidth", "minimumHeight")]
    suffixes = [theme + "-" + locale for theme in ("dark", "light") for locale in ("en", "ru")]
    names = {state + "-" + suffix + "-" + viewport + ".png" for state in contract['LibraryLayout']['states'] for suffix in suffixes for viewport in sizes}
    names |= {"settings" + section + state + "-" + suffix + "-" + size("settingsWidth", "settingsHeight") + ".png" for section in contract['SettingsDialog']['sections'] for state in contract['SettingsDialog']['states'] for suffix in suffixes}
    names |= {"player" + state + "-" + suffix + "-" + viewport + ".png" for state in contract['PlayerChrome']['states'] for suffix in suffixes for viewport in sizes}
    names |= {"playersettings" + section + state + "-" + suffix + "-" + size("settingsWidth", "playerSettingsHeight") + ".png" for section in contract['PlayerSettingsDialog']['sections'] for state in contract['PlayerSettingsDialog']['states'] for suffix in suffixes}
    call_sizes = [size("callInitialWidth", "callInitialHeight"), size("callGalleryWidth", "callGalleryHeight")]
    names |= {"call" + case + "-" + suffix + "-" + viewport + ".png" for case in contract["CallWindowShell"]["galleryCases"] for suffix in suffixes for viewport in call_sizes}
    names |= {"selection" + kind + state + "-" + suffix + "-" + size("selectionGalleryWidth", "selectionGalleryHeight") + ".png" for kind in contract["SelectionMenu"]["kinds"] for state in contract["SelectionMenu"]["states"] for suffix in suffixes}
    return {name.casefold() for name in names}


def retain_design_images(root: Path, payload: Path, target: str) -> None:
    source = root / "artifacts/design" / target
    if not source.exists():
        raise ValueError("Required design render matrix is missing")
    files = list(source.iterdir())
    expected = design_image_names(root)
    if source.is_symlink() or len(files) != len(expected) or {p.name.casefold() for p in files} != expected:
        raise ValueError("Design render matrix differs from the shared contract")
    destination = payload / "design"
    destination.mkdir()
    total = 0
    for path in sorted(files):
        if path.is_symlink() or not path.is_file():
            raise ValueError("Unsafe design image")
        total += path.stat().st_size
        if path.stat().st_size > 4 * 1024 * 1024 or total > 64 * 1024 * 1024:
            raise ValueError("Design image artifact exceeds its size bound")
        with path.open("rb") as image:
            if image.read(8) != b"\x89PNG\r\n\x1a\n":
                raise ValueError("Design image must be a PNG")
        shutil.copyfile(path, destination / path.name)


def prepare_archive(root: Path, directory: Path, target: str, architecture: str, versions: dict, report: Report, success: bool) -> Path:
    if target == "windows" and success and not any(case["name"] == WINDOWS_SERVICE_STAGE and case["success"] for case in report.cases):
        raise ValueError("Successful Windows qualification requires the admitted room service stage")
    payload = directory / "payload"
    payload.mkdir(parents=True, exist_ok=True)
    report.write(payload / "validation.xml")
    candidate = root / "artifacts/application-ci" / {"macos": "macos-arm64", "windows": "windows-x64", "linux": "linux-x64"}[target] / "candidate"
    if success:
        sys.path.insert(0, str(ROOT / "tools/application"))
        from candidate_tree import inventory
        receipt = json.loads((candidate / "candidate.json").read_text(encoding="utf-8"))
        if (receipt.get("schemaVersion") != 1
                or receipt.get("platform") != {"macos": "macos-arm64", "windows": "windows-x64", "linux": "linux-x64"}[target]
                or receipt.get("developmentCandidate") is not True or receipt.get("releaseReady") is not False
                or inventory(candidate / "contents") != receipt["files"]
                or receipt["sourceCommit"] != subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
                or receipt.get("sourceInputs", {}).get("commit") != receipt["sourceCommit"]
                or receipt.get("sourceInputs", {}).get("localChanges") is not False
                or any(value["kind"] == "link" for value in receipt["files"].values())):
            raise ValueError("Standalone application candidate is stale, incomplete or contains links")
        expected = {"candidate.json", *["contents/" + name for name, value in receipt["files"].items() if value["kind"] == "file"]}
        if {path.relative_to(candidate).as_posix() for path in candidate.rglob("*") if path.is_file()} != expected:
            raise ValueError("Standalone candidate includes undeclared files")
    if success and target == "linux":
        shutil.copy2(root / "artifacts/linux-boundary/report.json", payload / "linux-boundary.json")
    if success and target != "linux":
        retain_design_images(root, payload, target)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    manifest = {
        "schemaVersion": 1, "sourceCommit": commit,
        "sourceHead": os.environ.get("CI_HEAD_COMMIT", commit),
        "sourceTree": subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=root, text=True).strip(),
        "platform": target, "architecture": architecture, "validation": "success" if success else "failure",
        "validationScope": "native-build-and-standalone-package",
        "applicationBuilt": success, "guiAccepted": False, "physicalOutputAccepted": False,
        "runId": os.environ.get("GITHUB_RUN_ID"), "runAttempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "runner": {key: os.environ.get(key) for key in ("ImageOS", "ImageVersion", "RUNNER_OS", "RUNNER_ARCH")},
        "toolchains": versions,
        "files": {str(path.relative_to(payload)).replace("\\", "/"): sha256(path) for path in sorted(payload.rglob("*")) if path.is_file()},
    }
    if success:
        manifest["candidateReceiptSha256"] = sha256(candidate / "candidate.json")
    (payload / "build-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    output = directory / "artifact.zip"
    with tempfile.TemporaryDirectory(prefix=".native-archive-", dir=directory) as temporary:
        staged = Path(temporary) / "artifact.zip"
        with zipfile.ZipFile(staged, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(payload.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(payload))
            if success:
                # Keep one archive, without a second full runtime copy or nested ZIP.
                # Directory entries preserve even empty folders recorded by the
                # immutable candidate policy; files alone cannot round-trip them.
                for name, value in sorted(receipt["files"].items()):
                    if value["kind"] == "directory":
                        entry = zipfile.ZipInfo("candidate/contents/" + name + "/", (1980, 1, 1, 0, 0, 0))
                        entry.create_system = 3
                        entry.external_attr = (0o40755 << 16) | 0x10
                        archive.writestr(entry, b"")
                for name in sorted(expected):
                    path = candidate / name
                    entry = zipfile.ZipInfo("candidate/" + name, (1980, 1, 1, 0, 0, 0))
                    entry.create_system = 3
                    entry.external_attr = (path.stat().st_mode & 0xFFFF) << 16
                    entry.compress_type = zipfile.ZIP_DEFLATED
                    digest = hashlib.sha256()
                    with path.open("rb") as source, archive.open(entry, "w") as destination:
                        while chunk := source.read(1024 * 1024):
                            digest.update(chunk)
                            destination.write(chunk)
                    recorded = (manifest["candidateReceiptSha256"] if name == "candidate.json" else
                                receipt["files"][name.removeprefix("contents/")]["sha256"])
                    if digest.hexdigest() != recorded:
                        raise ValueError("Standalone candidate changed while archiving")
        if staged.stat().st_size > NATIVE_ARCHIVE_BOUND:
            raise ValueError("Native application archive exceeds its transport bound")
        staged.replace(output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=("macos", "windows", "linux"))
    args = parser.parse_args()
    specification = json.loads((ROOT / "tools/native/toolchains.json").read_text(encoding="utf-8"))[args.target]
    directory = ROOT / "artifacts/native" / args.target
    if directory.is_symlink() or not directory.resolve().is_relative_to(ROOT):
        raise ValueError("Native report directory must remain in the workspace")
    if directory.exists():
        shutil.rmtree(directory)
    (directory / "payload").mkdir(parents=True)
    report, versions = Report(), {}
    architecture, success = "unverified", False
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("ACTIONS_", "INPUT_")) and key != "GITHUB_TOKEN"}
    dotnet = None
    try:
        architecture = report.execute("Verify runner architecture", lambda: check_host(args.target))
        report.execute("Native application candidate staging policy", lambda: subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tools/application/tests", "-v"],
            cwd=ROOT, env=environment, check=True))
        report.execute("Native renderer source and header boundaries", lambda: subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tools/desktop/tests", "-v"],
            cwd=ROOT, env=environment, check=True))
        report.execute("Native video/audio source and loader boundaries", lambda: subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tools/vlc/tests", "-v"],
            cwd=ROOT, env=environment, check=True))
        if args.target == "macos":
            def select_toolchain():
                if environment.get("GITHUB_ACTIONS") == "true":
                    environment["DEVELOPER_DIR"] = f"/Applications/Xcode_{specification['ciXcodeVersion']}.app/Contents/Developer"
                for tool, command in (("swift", ["swift", "--version"]), ("xcode", ["xcodebuild", "-version"])):
                    versions[tool] = subprocess.check_output(command, cwd=ROOT, env=environment, text=True).strip()
                if environment.get("GITHUB_ACTIONS") == "true" and versions["xcode"].splitlines()[0] != f"Xcode {specification['ciXcodeVersion']}":
                    raise ValueError("Selected Xcode differs from the native CI pin")
            report.execute("Select Apple toolchain", select_toolchain)
            command = [sys.executable, "tools/native/check_macos.py"]
        elif args.target == "windows":
            temporary = Path(os.environ["RUNNER_TEMP"])
            dotnet = report.execute("Provision exact .NET SDK", lambda: ensure_dotnet(specification, temporary))
            versions["dotnet"] = subprocess.check_output([str(dotnet), "--version"], cwd=ROOT, text=True).strip()
            command = ["pwsh", "-NoProfile", "-File", "tools/native/check_windows.ps1", "-DotNet", str(dotnet)]
        else:
            if os.environ.get("GITHUB_ACTIONS") == "true":
                report.execute("Restore and verify developer SDK archives", lambda: subprocess.run(
                    ["node", "tools/ci/sdk_cache.mjs", "--current-run", "--platform", "linux-x64",
                     "--sdk", "jdk", "--sdk", "kotlin"],
                    cwd=ROOT, check=True))
            report.execute("Provision exact developer JDK", lambda: subprocess.run(
                sdk_command("jdk", "linux-x64"), cwd=ROOT, env=environment, check=True))
            sys.path.insert(0, str(ROOT / "tools/jvm"))
            from jdk import home
            versions["javac"] = subprocess.check_output([str(home() / "bin/javac"), "--version"], text=True).strip()
            report.execute("Provision exact developer Kotlin compiler", lambda: subprocess.run(
                sdk_command("kotlin", "linux-x64"), cwd=ROOT, env=environment, check=True))
            versions["kotlin"] = json.loads((ROOT / "tools/jvm/kotlin_toolchain.json").read_text())["version"]
            report.execute("Reuse this run's verified Kotlin dependency", lambda:
                           restore_current_dependency_cache("kotlin-stdlib", "linux-x64"))
            report.execute("Build and verify owned Kotlin runtime and shared domain", lambda: subprocess.run(
                [sys.executable, "tools/dependencies/build.py", "build", "--component", "kotlin-stdlib", "--platform", "linux-x64"],
                cwd=ROOT, env=environment, check=True))
            command = [sys.executable, "tools/native/check_linux.py"]
        stage = "Linux Kotlin domain and JVM boundary checks" if args.target == "linux" else "Native formatting, domain tests, and application build"
        report.execute(stage, lambda: subprocess.run(command, cwd=ROOT, env=environment, check=True))
        if args.target == "windows" and os.environ.get("GITHUB_ACTIONS") == "true":
            report.execute("Restore and verify native media developer archives", lambda: subprocess.run(
                ["node", "tools/ci/sdk_cache.mjs", "--current-run", "--platform", "windows-x64", "--sdk", "windows-vlc"],
                cwd=ROOT, check=True))
        package = [sys.executable, "tools/application/ci.py", "--target",
                   {"macos": "macos-arm64", "windows": "windows-x64", "linux": "linux-x64"}[args.target]]
        if dotnet:
            package += ["--dotnet", str(dotnet)]
        report.execute("Assemble complete standalone native application", lambda: subprocess.run(package, cwd=ROOT, check=True))
        if args.target == "windows":
            report.execute("Prepare pinned server contract validator", lambda: subprocess.run(
                [sys.executable, "tools/dependencies/build.py", "prepare", "--component", "fastjsonschema", "--platform", "windows-x64"],
                cwd=ROOT, env=environment, check=True))
            runtime = ROOT / "artifacts/application-ci/windows-x64/candidate/contents/SyncWatch/Syncplay"
            report.execute(WINDOWS_SERVICE_STAGE, lambda: subprocess.run(
                [sys.executable, "tools/server/check.py", "--dotnet", str(dotnet), "--admission-runtime", str(runtime)],
                cwd=ROOT, env=environment, check=True))
        report.execute("Verify unchanged tracked source", lambda: subprocess.run(["git", "diff", "--exit-code"], cwd=ROOT, check=True))
        success = True
    except Exception:
        print(f"Native {args.target} validation failed; consult the failing stage in the job log", file=sys.stderr)
    finally:
        prepare_archive(ROOT, directory, args.target, architecture, versions, report, success)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
