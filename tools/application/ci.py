"""Assemble the complete native development application from this CI run's verified SDKs."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from ci_inputs import import_dependency, import_helper
from receipts import ROOT, require_host


def run(*command):
    # Build tools and product subprocesses never inherit Actions service tokens.
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("ACTIONS_", "INPUT_")) and key != "GITHUB_TOKEN"}
    subprocess.run(list(map(str, command)), cwd=ROOT, env=environment, check=True)


def restore(name, target, directory, *, helper=False):
    prefix = f"syncwatch-helper-{target}" if helper else f"syncwatch-dependency-{name}-{target}"
    archive = directory / (name + ".zip")
    downloaded = subprocess.check_output(["node", "tools/application/fetch_input.mjs", prefix, str(archive)],
                                         cwd=ROOT, text=True)
    identity = json.loads(downloaded)
    options = dict(target=target, run_id=int(os.environ["GITHUB_RUN_ID"]),
                   producer_attempt=identity["producerAttempt"])
    try:
        return (import_helper(archive, directory / name, **options) if helper else
                import_dependency(archive, directory / name, component=name, **options))
    finally:
        archive.unlink(missing_ok=True)


def linux_inputs(directory, media, desktop):
    from linux_build_tools import prepare
    sys.path.insert(0, str(ROOT / "tools/jvm"))
    import jdk
    # These submodules are source inputs only. The developer JDK stays a compiler
    # and boot tool; only our original-source JVM is accepted by package.py.
    profiles = [ROOT / "tools/application" / name for name in ("jvm-profile.json", "linux-clients.json")]
    sources = sorted({item["path"] for profile in profiles
                      for item in json.loads(profile.read_text())["sources"].values()})
    sources.append("3rdparty/desktop-dbus")  # Original public headers for the isolated account store adapter.
    run("git", "submodule", "update", "--init", "--checkout", "--depth=1", "--", *sources)
    with tempfile.TemporaryDirectory(prefix=".linux-build-inputs-", dir=directory) as temporary:
        work = Path(temporary)
        macros, autotools = prepare(media, work / "tools")
        # GNU config and pkg-config macros belong to the provisioned build SDK;
        # their exact bytes are recorded in the source-built JVM receipt.
        auxiliary = Path("/usr/share/misc")
        pkg_macros = Path("/usr/share/aclocal")
        jvm_sources, client_sources = work / "jvm-originals", work / "client-originals"
        run(sys.executable, "tools/application/jvm_sources.py", "--output", jvm_sources)
        run(sys.executable, "tools/application/jvm_sources.py", "--profile", profiles[1], "--output", client_sources)
        common = ["--media-package", media, "--autotools", autotools, "--xorg-macros", macros,
                  "--gnu-config", auxiliary]
        cache = os.environ.get("GITHUB_ACTIONS") == "true"
        request = directory / "runtime-cache-request.json"
        hit = False
        if cache:
            run(sys.executable, "tools/ci/linux_runtime_cache.py", "prepare", directory,
                "--media", media, "--autotools", autotools, "--xorg-macros", macros,
                "--gnu-config", auxiliary, "--pkg-config-macros", pkg_macros,
                "--jvm-sources", jvm_sources, "--client-sources", client_sources)
            restored = subprocess.check_output(["node", "tools/ci/linux_runtime_cache.mjs", "restore", str(request)],
                                               cwd=ROOT, text=True)
            hit = json.loads(restored)["hit"]
        if not hit:
            run(sys.executable, "tools/application/jvm_runtime.py", "--sources", jvm_sources,
                *common, "--boot-jdk", jdk.home(), "--pkg-config-macros", pkg_macros,
                "--jobs", "2", "--output", directory / "jvm")
            run(sys.executable, "tools/application/linux_clients.py", "--sources", client_sources,
                *common, "--patchelf", shutil.which("patchelf"), "--output", directory / "clients")
        trusted = os.environ.get("GITHUB_EVENT_NAME") in {"push", "schedule", "workflow_dispatch"} \
            and os.environ.get("GITHUB_REF") == "refs/heads/main"
        if cache and (trusted or not hit):
            run(sys.executable, "tools/ci/linux_runtime_cache.py", "stage", request)
            if not hit:
                run(sys.executable, "tools/ci/linux_runtime_cache.py", "validate-staged", request)
    run(sys.executable, "tools/application/build_linux.py", "--toolchain-root", ROOT,
        "--desktop-sdk", desktop, "--output", directory / "application")
    return jdk.home()


def build(target, dotnet=None):
    require_host(target)
    directory = ROOT / "artifacts/application-ci" / target
    directory.mkdir(parents=True, exist_ok=False)
    try:
        media = restore("libvlc", target, directory)
        helper = restore("helper", target, directory, helper=True)
        command = [sys.executable, "tools/application/package.py", "--target", target,
                   "--media-package", media, "--helper-package", helper,
                   "--output", directory / "candidate"]
        if target == "macos-arm64":
            command += ["--application", ROOT / "artifacts/macos/SyncWatch.app/Contents/MacOS/SyncWatch"]
        else:
            # SDK artifacts contain the runtime closure. The first-party C
            # bridge separately compiles against the registered public headers.
            run("git", "submodule", "update", "--init", "--checkout", "--depth=1", "--", "3rdparty/vlc")
            run(sys.executable, "tools/vlc/provision_sdks.py")
            # The native bridge has no external binary inputs. Compile its
            # checked-in ABI on this OS, then bind it to the imported Media SDK.
            # Use a separate process: several tool entrypoints have modules
            # named build/source_profile and must not share an import namespace.
            bridge = directory / "bridge"
            run(sys.executable, "tools/playback/build.py", "--package", media, "--output", bridge)
            if target == "windows-x64":
                if dotnet is None:
                    raise ValueError("Windows packaging requires the validated native .NET SDK")
                command += ["--application", ROOT / "apps/windows/SyncWatch/SyncWatch.csproj",
                            "--dotnet", dotnet, "--bridge", bridge / "syncwatch_playback.dll"]
            else:
                desktop = restore("compose-ui-jvm", target, directory)
                renderer = restore("skiko-native-objects", target, directory)
                home = linux_inputs(directory, media, desktop)
                run(sys.executable, "tools/application/check_linux_design.py", "--toolchain-root", ROOT,
                    "--application", directory / "application/application.jar", "--desktop-sdk", desktop,
                    "--renderer-package", renderer)
                run("cc", "-std=c11", "-shared", "-fPIC", "-O2", "-g0", "-Wall", "-Wextra", "-Werror",
                    "-I" + str(home / "include"), "-I" + str(home / "include/linux"), "-I" + str(media / "include"),
                    ROOT / "packages/linux-playback/native/jni.c", ROOT / "packages/linux-playback/native/x11.c",
                    bridge / "syncwatch_playback.so", "-ldl", "-Wl,-rpath,$ORIGIN", "-o", bridge / "syncwatch_playback_jni.so")
                command += ["--application", directory / "application/application.jar", "--bridge", bridge,
                            "--desktop-sdk", desktop, "--renderer", renderer,
                            "--jvm", directory / "jvm/payload", "--linux-clients", directory / "clients/payload"]
        run(*command)
        return directory / "candidate"
    except BaseException:
        # Runtime source builds retain their diagnostics, never a partial app.
        if (directory / "candidate").exists():
            shutil.rmtree(directory / "candidate")
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, choices=("macos-arm64", "windows-x64", "linux-x64"))
    parser.add_argument("--dotnet", type=Path)
    args = parser.parse_args()
    print(build(args.target, args.dotnet))
