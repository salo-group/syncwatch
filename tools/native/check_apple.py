"""Format-check, build, and test the macOS shell with platform SDK tools."""

import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[2]


def run(*arguments: str, **options) -> None:
    subprocess.run(arguments, cwd=ROOT, check=True, **options)


def main() -> None:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise SystemExit("The initial macOS acceptance lane requires macOS arm64")
    toolchain = json.loads((ROOT / "tools/native/toolchains.json").read_text())["apple"]
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
    environment["SYNCWATCH_CONTRACT_FIXTURES"] = str(ROOT / "contracts/fixtures/acceptance.json")
    run("swift", "test", "--package-path", "packages/apple-domain", "-Xswiftc", "-warnings-as-errors", env=environment)
    run("swift", "build", "--package-path", "apps/apple", "-Xswiftc", "-warnings-as-errors")
    binary_directory = subprocess.check_output(
        ["swift", "build", "--package-path", "apps/apple", "--show-bin-path"], cwd=ROOT, text=True,
    ).strip()
    bundle = ROOT / "artifacts/apple/SyncWatch.app/Contents"
    (bundle / "MacOS").mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(binary_directory) / "SyncWatch", bundle / "MacOS/SyncWatch")
    shutil.copy2(ROOT / "apps/apple/Resources/Info.plist", bundle / "Info.plist")
    run("plutil", "-lint", str(bundle / "Info.plist"))
    print("Built artifacts/apple/SyncWatch.app; interactive launch/close is a separate acceptance step")


if __name__ == "__main__":
    main()
