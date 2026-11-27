"""Own bounded upstream CPython builds and identify installed SDK tools."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from configuration import unix_setup, windows_setup, relocatable_makefile, assert_unix_membership
from build_metadata import BUILD_DATE, BUILD_TIME, SOURCE_DATE_EPOCH
from source_profile import ROOT, materialize

sys.path.insert(0, str(ROOT / "tools/compression"))
from toolchain import resolve, checksum, output

def sdk_identity():
    cmake, options, identity = resolve()
    identity = dict(identity, buildPython=sys.version.split()[0], buildPythonSha256=checksum(sys.executable))
    if identity["target"] == "windows-x64":
        instance = next(v.split("=", 1)[1] for v in options if v.startswith("-DCMAKE_GENERATOR_INSTANCE="))
        msbuild = Path(instance) / "MSBuild/Current/Bin/MSBuild.exe"
        if not msbuild.is_file():
            raise ValueError("Selected Visual Studio SDK is missing MSBuild")
        identity.update(msbuildSha256=checksum(msbuild), msbuildVersion=output([str(msbuild), "-version", "-nologo"]))
    return cmake, options, identity


def execute(name, args, cwd, logs, env=None, timeout=1800):
    path = logs / (name + ".log")
    with path.open("wb") as out:
        result = subprocess.run(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                stdout=out, stderr=subprocess.STDOUT, timeout=timeout)
    if result.returncode:
        diagnostic = path.read_bytes()[-16000:].decode("utf-8", errors="replace")
        raise ValueError("Native interpreter " + name + " failed:\n" + diagnostic)
    return {"command": args, "logSha256": checksum(path)}


def build_native(work, sources, profile, cmake, options, sdk, dependencies=None):
    dependencies = dependencies or {}
    source, zlib, build, logs = (work / name for name in ["cpython", "zlib", "build", "logs"])
    logs.mkdir(parents=True)
    materialize(source, sources["cpython"], profile["source"]["executables"])
    materialize(zlib, sources["zlib"], profile["zlib"]["executables"])
    commands = {}
    environment = os.environ.copy()
    environment.update(SOURCE_DATE_EPOCH=SOURCE_DATE_EPOCH, PYTHONHASHSEED="0", PYTHONDONTWRITEBYTECODE="1")
    target = sdk["target"]
    if target == "windows-x64":
        windows_setup(source, profile)
        instance = next(v.split("=", 1)[1] for v in options if v.startswith("-DCMAKE_GENERATOR_INSTANCE="))
        msbuild = str(Path(instance) / "MSBuild/Current/Bin/MSBuild.exe")
        toolset = next(options[n + 1].split(",", 1)[0] for n, v in enumerate(options) if v == "-T")
        properties = ["/p:Configuration=Release", "/p:Platform=x64", "/p:PlatformToolset=" + toolset,
            "/p:VCToolsVersion=" + sdk["compiler"].split()[1], "/p:WindowsTargetPlatformVersion=" + sdk["sdk"],
            "/p:IncludeExtensions=false", "/p:IncludeTests=false", "/p:IncludeTest=false", "/p:IncludeExternals=true",
            "/p:IncludeSSL=false", "/p:IncludeTkinter=false", "/p:IncludeCTypes=false", "/p:KillPython=false",
            '/p:PythonForBuild="' + sys.executable + '"', "/p:zlibDir=" + str(zlib) + "\\",
            "/p:PreferredToolArchitecture=x64",
            "/p:ForceImportAfterCppTargets=" + str(ROOT / "tools/python-build/build_metadata.targets"),
            "/p:SyncWatchBuildDate=" + BUILD_DATE, "/p:SyncWatchBuildTime=" + BUILD_TIME]
        properties.extend(dependencies.get("msbuildProperties", []))
        projects = [("interpreter", "pcbuild.proj"),
                    *((name.removeprefix("_"), name + ".vcxproj") for name in profile.get("windowsShared", ["_socket", "select"]))]
        for name, project in projects:
            commands[name] = execute(name, [msbuild, str(source / "PCbuild" / project), "/m:4", "/nologo", "/v:minimal", *properties], source, logs, environment)
        native = source / "PCbuild/amd64"
        paths = {"executable": native / "python.exe", "runtime": native / "python312.dll",
                 "library": native / "python312.lib", "config": source / "PC/pyconfig.h",
                 "extensions": [native / (name + ".pyd") for name in profile.get("windowsShared", ["_socket", "select"])]}
    else:
        zbuild = work / "zlib-build"
        commands["zlib-configure"] = execute("zlib-configure", [cmake, "-S", (ROOT / "tools/python-build/zlib").as_posix(), "-B", zbuild.as_posix(),
            *options, "-DCMAKE_BUILD_TYPE=Release", "-DSYNCWATCH_ZLIB_SOURCE=" + zlib.as_posix()], work, logs, environment)
        commands["zlib-build"] = execute("zlib-build", [cmake, "--build", zbuild.as_posix(), "--target", "zlibstatic", "--parallel", "4"], work, logs, environment)
        library = Path((zbuild / "library-Release.txt").read_text())
        unix_setup(source, build, zlib, library, profile, dependencies.get("moduleFlags"))
        compiler = next(v.split("=", 1)[1] for v in options if v.startswith("-DCMAKE_C_COMPILER="))
        environment.update(CC=compiler, CONFIG_SITE="/dev/null", CFLAGS="-O2 -g0 -ffile-prefix-map=" + work.as_posix() + "=syncwatch-build")
        environment.update(dependencies.get("configureEnvironment", {}))
        if target == "macos-arm64":
            environment["MACOSX_DEPLOYMENT_TARGET"] = "13.0"
            # New SDKs can weak-link pipe2 even though the supported macOS
            # deployment target does not provide it. Select upstream's pipe
            # and fcntl fallback instead of calling a missing weak symbol.
            environment["ac_cv_func_pipe2"] = "no"
            environment["SDKROOT"] = next(v.split("=", 1)[1] for v in options if v.startswith("-DCMAKE_OSX_SYSROOT="))
            environment["LDFLAGS"] = "-Wl,-rpath,@loader_path/../lib"
        else:
            # Two levels of make/shell parsing preserve the literal loader token.
            environment["LDFLAGS"] = "-Wl,-rpath,'$$ORIGIN/../lib'"
        commands["configure"] = execute("configure", ["sh", (source / "configure").as_posix(), "--prefix=/syncwatch/runtime", "--enable-shared",
            "--without-ensurepip", "--without-static-libpython", "--with-pkg-config=no", "--without-readline", "--disable-test-modules"], build, logs, environment)
        relocatable_makefile(build, target)
        assert_unix_membership(build, profile)
        commands["interpreter"] = execute("interpreter", ["make", "-j4", "python.exe" if target == "macos-arm64" else "python"], build, logs, environment)
        assert_unix_membership(build, profile)
        suffix = "dylib" if target == "macos-arm64" else "so.1.0"
        paths = {"executable": build / ("python.exe" if target == "macos-arm64" else "python"),
                 "runtime": build / ("libpython3.12." + suffix), "library": build / ("libpython3.12." + suffix),
                 "config": build / "pyconfig.h", "extensions": []}
    if not all(paths[name].is_file() for name in ["executable", "runtime", "library", "config"]):
        raise ValueError("Source-built interpreter outputs are incomplete")
    build_info = "Modules/getbuildinfo.c"
    if (source / build_info).read_bytes() != sources["cpython"][build_info]:
        raise ValueError("CPython build information source changed during the build")
    return source, paths, commands
