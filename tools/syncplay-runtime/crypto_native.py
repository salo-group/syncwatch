"""Build the original CFFI backend and cryptography extension against owned Python."""

import os
from pathlib import Path
import shutil
import sys

from source_graph import ROOT
from rust_graph import configure as configure_cargo, verify_metadata

sys.path.insert(0, str(ROOT / "tools/python-build"))
from configuration import unix_make
from native_build import execute
from runtime_package import write_files
from source_profile import stdlib_archive

PYTHON_INSTALL_DIR = "python3.12"


def python_sdk(work, profile, sources, source, native, openssl, target, environment):
    sdk = work / "python-sdk"
    windows = target == "windows-x64"
    directory = sdk / ("bin" if windows else "lib")
    include = "include/" + PYTHON_INSTALL_DIR + "/"
    files = {include + n.removeprefix("Include/"): b for n, b in sources["cpython"].items()
             if n.startswith("Include/") and n.endswith(".h")}
    files[include + "pyconfig.h"] = native["config"].read_bytes()
    files[("bin" if windows else "lib") + "/python312.zip"] = stdlib_archive(sources, profile)
    write_files(sdk, files)
    libraries = sdk / ("libs" if windows else "lib")
    libraries.mkdir(exist_ok=True)
    shutil.copyfile(native["runtime"], directory / native["runtime"].name)
    if windows:
        shutil.copyfile(native["library"], libraries / native["library"].name)
    if target == "linux-x64":
        # The linker resolves -lpython3.12 through its unversioned SDK name;
        # the application's loaded SONAME remains libpython3.12.so.1.0.
        shutil.copyfile(native["library"], sdk / "lib/libpython3.12.so")
    for path in native["extensions"]:
        shutil.copyfile(path, directory / path.name)
    if windows:
        for name in ["python3.dll", "python3.lib"]:
            path = native["runtime"].parent / name
            shutil.copyfile(path, (directory if name.endswith(".dll") else libraries) / name)
        for path in (openssl / "bin").glob("*.dll"):
            shutil.copyfile(path, directory / path.name)
    else:
        # Upstream's bootstrap executable has a loader-relative dependency.
        shutil.copyfile(native["runtime"], source.parent / "lib" / native["runtime"].name)
        suffix = ".3.dylib" if target == "macos-arm64" else ".so.3"
        for name in ["libssl", "libcrypto"]:
            shutil.copyfile(openssl / "lib" / (name + suffix), directory / (name + suffix))
        logs = work / "python-sdk-logs"; logs.mkdir()
        makefile = native["runtime"].parent / "Makefile"
        configured = makefile.read_bytes()
        execute("sysconfig", unix_make("pybuilddir.txt"), native["runtime"].parent, logs, environment)
        if makefile.read_bytes() != configured or native["runtime"].read_bytes() != (directory / native["runtime"].name).read_bytes():
            raise ValueError("CPython sysconfig generation changed its Makefile or native runtime")
        generated = list(native["runtime"].parent.glob("build/lib.*/_sysconfigdata_*.py"))
        if len(generated) != 1:
            raise ValueError("Source-built Python build configuration is missing")
        shutil.copyfile(generated[0], directory / generated[0].name)
    return sdk, directory


def cffi(source, work, python, ffi, cmake, options, native, environment):
    build = work / "cffi-build"; logs = work / "cffi-logs"; logs.mkdir()
    sdk, directory = python
    library = sdk / {"windows-x64": "libs/python312.lib", "macos-arm64": "lib/libpython3.12.dylib",
                     "linux-x64": "lib/libpython3.12.so.1.0"}[native["target"]]
    include = (sdk / "include" / PYTHON_INSTALL_DIR).as_posix()
    arguments = [cmake, "-S", (ROOT / "tools/syncplay-runtime/cffi").as_posix(), "-B", build.as_posix(), *options,
        "-DCMAKE_BUILD_TYPE=Release", "-DSYNCWATCH_CFFI_SOURCE=" + source.as_posix(),
        "-DSYNCWATCH_PYTHON_INCLUDE=" + include, "-DSYNCWATCH_PYTHON_CONFIG=" + include,
        "-DSYNCWATCH_PYTHON_LIBRARY=" + library.as_posix()]
    if ffi is not None:
        arguments += ["-DSYNCWATCH_FFI_INCLUDE=" + (ffi / "include").as_posix(), "-DSYNCWATCH_FFI_LIBRARY=" + (ffi / "lib/libffi.a").as_posix()]
    commands = {"configure": execute("configure", arguments, work, logs, environment)}
    commands["compile"] = execute("compile", [cmake, "--build", str(build), "--config", "Release", "--parallel", "4"], work, logs, environment)
    return Path((build / "library-Release.txt").read_text()), commands


def cryptography(paths, work, python, core, openssl, backend, rust, native, environment):
    sdk, directory = python
    root = configure_cargo(work, paths)
    windows = native["target"] == "windows-x64"
    libraries = sdk / ("libs" if windows else "lib")
    config = work / "pyo3-config.txt"
    config.write_text("implementation=CPython\nversion=3.12\nshared=true\nabi3=true\nlib_name="
        + ("python3" if windows else "python3.12") + "\nlib_dir=" + str(libraries)
        + "\npointer_width=64\nbuild_flags=\nsuppress_build_script_link_lines=false\n", encoding="utf-8")
    env = dict(environment)
    for key in list(env):
        if key.startswith(("RUSTFLAGS", "RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER", "CARGO_")):
            del env[key]
    env.update(CARGO_HOME=str(work / "empty-cargo-home"), CARGO_TARGET_DIR=str(work / "rust-target"),
        CARGO_NET_OFFLINE="true", PYO3_CONFIG_FILE=str(config), PYO3_PYTHON=str(core["executable"]),
        PYTHONHOME=str(sdk), PYTHONPATH=os.pathsep.join([str(directory / "python312.zip"), str(directory), str(paths["cffi"] / "src"),
            str(paths["pycparser"]), str(backend.parent)]),
        # PyO3 reads the explicit config above. The original CFFI build script
        # uses this layout to find the headers paired with that config.
        PYO3_CROSS_LIB_DIR=str(libraries / PYTHON_INSTALL_DIR), OPENSSL_DIR=str(openssl), OPENSSL_STATIC="0",
        OPENSSL_NO_PKG_CONFIG="1", OPENSSL_LIBS="libssl:libcrypto" if windows else "ssl:crypto",
        VCPKGRS_DISABLE="1", CRYPTOGRAPHY_BUILD_OPENSSL_NO_LEGACY="1")
    env["PATH"] = str(directory) + os.pathsep + env["PATH"]
    flags = ["--remap-path-prefix=" + str(work) + "=syncwatch-build", "-C", "debuginfo=0"]
    if native["target"] == "macos-arm64":
        flags += ["-C", "link-arg=-Wl,-rpath,@loader_path/../../.."]
    elif not windows:
        flags += ["-C", "link-arg=-Wl,-rpath,$ORIGIN/../../.."]
    env["CARGO_ENCODED_RUSTFLAGS"] = "\x1f".join(flags)
    suffix = ".exe" if windows else ""
    cargo = rust / ("bin/cargo" + suffix)
    graph = verify_metadata(cargo, root, paths, env)
    logs = work / "cryptography-logs"; logs.mkdir()
    command = execute("compile", [str(cargo), "build", "--offline", "--locked", "--release", "-p", "cryptography-rust"],
                      root, logs, env)
    name = {"macos-arm64": "libcryptography_rust.dylib", "linux-x64": "libcryptography_rust.so",
            "windows-x64": "cryptography_rust.dll"}[native["target"]]
    result = work / "rust-target/release" / name
    if not result.is_file():
        raise ValueError("Source-built cryptography extension is missing")
    commands = {"command": command, "crateAuthorities": graph}
    if native["target"] == "macos-arm64":
        commands["libraryIdentity"] = execute("library-identity", ["install_name_tool", "-id",
            "@rpath/_rust.abi3.so", str(result)], root, logs, env)
    return result, commands
