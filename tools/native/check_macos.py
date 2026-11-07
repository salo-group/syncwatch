"""Format-check, build, and test the macOS shell with platform SDK tools."""

import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def run(*arguments: str, **options) -> None:
    subprocess.run(arguments, cwd=ROOT, check=True, **options)


def main() -> None:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise SystemExit("The initial macOS acceptance lane requires macOS arm64")
    toolchain = json.loads((ROOT / "tools/native/toolchains.json").read_text())["macos"]
    for arguments, expression, minimum in (
        (("swift", "--version"), r"Swift version (\d+)\.(\d+)", toolchain["minimumSwiftCompiler"]),
        (("xcodebuild", "-version"), r"Xcode (\d+)\.(\d+)", toolchain["minimumXcode"]),
    ):
        output = subprocess.check_output(arguments, text=True)
        print(output, end="", flush=True)
        version = re.search(expression, output)
        if version is None or tuple(map(int, version.groups())) < tuple(map(int, minimum.split("."))):
            raise SystemExit("The installed Apple toolchain does not meet the compatibility floor")
    sources = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", "*.swift"], cwd=ROOT, text=True,
    ).splitlines()
    run("swift", "format", "lint", "--strict", "--configuration", ".swift-format", *sources)
    environment = os.environ.copy()
    # The first-party C interop target compiles against the pinned public headers.
    # Runtime libraries remain the separately source-built media SDK.
    run("git", "submodule", "update", "--init", "--checkout", "--depth=1", "--", "3rdparty/vlc")
    run("swift", "test", "--package-path", "packages/macos-room", "-Xswiftc", "-warnings-as-errors", env=environment)
    environment["SYNCWATCH_LIBRARY_FIXTURE"] = str(ROOT / "contracts/fixtures/library-catalog.json")
    run("swift", "test", "--package-path", "packages/macos-library", "-Xswiftc", "-warnings-as-errors", env=environment)
    environment["SYNCWATCH_CONTRACT_FIXTURES"] = str(ROOT / "contracts/fixtures/acceptance.json")
    run("swift", "test", "--package-path", "packages/macos-bridge", "-Xswiftc", "-warnings-as-errors", env=environment)
    run("swift", "test", "--package-path", "packages/macos-domain", "-Xswiftc", "-warnings-as-errors", env=environment)
    run("swift", "test", "--package-path", "packages/macos-playback", "-Xswiftc", "-warnings-as-errors", env=environment)
    shutil.rmtree(ROOT / "artifacts/design/macos", ignore_errors=True)
    environment["SYNCWATCH_DESIGN_SCREENSHOTS"] = str(ROOT / "artifacts/design/macos")
    run("swift", "test", "--package-path", "packages/design", "-Xswiftc", "-warnings-as-errors", env=environment)
    run(sys.executable, "tools/consumers/admission_fixture.py", "--",
        "swift", "test", "--package-path", "apps/macos", "-Xswiftc", "-warnings-as-errors")
    run("swift", "build", "--package-path", "apps/macos", "-Xswiftc", "-warnings-as-errors")
    binary_directory = subprocess.check_output(
        ["swift", "build", "--package-path", "apps/macos", "--show-bin-path"], cwd=ROOT, text=True,
    ).strip()
    bundle = ROOT / "artifacts/macos/SyncWatch.app/Contents"
    (bundle / "MacOS").mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(binary_directory) / "SyncWatch", bundle / "MacOS/SyncWatch")
    shutil.copy2(ROOT / "apps/macos/Resources/Info.plist", bundle / "Info.plist")
    run("plutil", "-lint", str(bundle / "Info.plist"))
    print("Built artifacts/macos/SyncWatch.app; interactive launch/close is a separate acceptance step")


if __name__ == "__main__":
    main()
