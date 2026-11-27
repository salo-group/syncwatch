"""Build selected upstream OpenSSL and libffi libraries with the identified SDK."""

import os
from pathlib import Path
import shutil
import sys

from source_graph import ROOT

sys.path.insert(0, str(ROOT / "tools/python-build"))
from native_build import execute


def openssl(source, work, native, perl, environment):
    build, stage, logs = (work / n for n in ["openssl-build", "openssl-stage", "openssl-logs"])
    build.mkdir(); logs.mkdir()
    env = dict(environment, PERL5LIB=str(source / "external/perl/Text-Template-1.56/lib"))
    target = {"macos-arm64": "darwin64-arm64-cc", "linux-x64": "linux-x86_64", "windows-x64": "VC-WIN64A"}[native["target"]]
    windows = native["target"] == "windows-x64"
    installation = "C:/SyncWatch/OpenSSL" if windows else "/syncwatch/openssl"
    prefix = stage / installation.split(":/")[-1].lstrip("/")
    flags = ["shared", "no-asm", "no-tests", "no-legacy", "no-comp", "no-engine", "no-module", "no-dso",
             "no-autoload-config", "--prefix=" + installation, "--openssldir=ssl", "--libdir=lib"]
    if windows:
        env["CC"] = "cl"
    configure = Path(os.path.relpath(source / "Configure", build)).as_posix()
    commands = {"configure": execute("configure", [str(perl), configure, target, *flags], build, logs, env)}
    # CreateProcess searches the parent's PATH, not the supplied child PATH.
    # Resolve nmake from the identified MSVC SDK before spawning it.
    make = env["SYNCWATCH_NMAKE"] if windows else "make"
    commands["compile"] = execute("compile", [make, *([] if windows else ["-j4"]), "build_libs"], build, logs, env)
    commands["install"] = execute("install", [make, "DESTDIR=" + stage.as_posix(), "install_dev"], build, logs, env)
    if native["target"] == "macos-arm64":
        for name in ["libcrypto.3.dylib", "libssl.3.dylib"]:
            commands["identity-" + name] = execute("identity-" + name,
                ["install_name_tool", "-id", "@rpath/" + name, str(prefix / "lib" / name)], build, logs, env)
        commands["crypto-reference"] = execute("crypto-reference", ["install_name_tool", "-change",
            installation + "/lib/libcrypto.3.dylib", "@rpath/libcrypto.3.dylib", str(prefix / "lib/libssl.3.dylib")], build, logs, env)
    return prefix, commands


def libffi(source, work, native, environment):
    if native["target"] == "windows-x64":
        return None, {}  # CFFI's original MSVC port is compiled from its own sources.
    build, prefix, logs = (work / n for n in ["libffi-build", "libffi-sdk", "libffi-logs"])
    build.mkdir(); logs.mkdir()
    commands = {"autogen": execute("autogen", ["sh", "autogen.sh"], source, logs, environment)}
    commands["configure"] = execute("configure", ["sh", str(source / "configure"), "--prefix=" + str(prefix),
        "--disable-shared", "--enable-static", "--disable-docs", "--with-pic"], build, logs, environment)
    commands["compile"] = execute("compile", ["make", "-j4"], build, logs, environment)
    commands["install"] = execute("install", ["make", "install"], build, logs, environment)
    return prefix, commands
