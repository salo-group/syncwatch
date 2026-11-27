"""Compile and exercise first-party native startup against the installed development SDK."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from generate_policy import GENERATED, PROFILE, ROOT, render
from native_sdk import resolve, sha

sys.path.insert(0, str(ROOT / "tools/compression"))
from toolchain import resolve as c_toolchain


CASES = ("success", "wrong-thread", "foreign", "init-failure", "adjacent-config", "script-error", "finalize-error")


def diagnostics(data, cwd):
    message = data.decode("utf-8", errors="replace")
    private = {}
    for path, label in [(Path.home(), "[home]"), (Path(tempfile.gettempdir()), "[temp]"),
                        (ROOT, "[source]"), (Path(cwd), "[fixture]")]:
        for directory in {path.absolute(), path.resolve()}:
            if directory == directory.parent:
                continue
            for name in {str(directory), directory.as_posix()}:
                private[name] = label
                private[name.replace("\\", "\\\\")] = label
    flags = re.IGNORECASE if sys.platform == "win32" else 0
    for name in sorted(private, key=len, reverse=True):
        message = re.sub(re.escape(name), private[name], message, flags=flags)
    return message


def execute(args, cwd, environment=None, timeout=120):
    with tempfile.TemporaryFile() as output:
        try:
            result = subprocess.run(args, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                                    stdout=output, stderr=subprocess.STDOUT, timeout=timeout)
        except subprocess.TimeoutExpired:
            output.seek(0)
            data = output.read(8192)
            raise ValueError("Native SDK command timed out:\n" + diagnostics(data, cwd)) from None
        except OSError as error:
            raise ValueError(f"Native SDK command could not start (OS error {error.errno}).") from None
        output.seek(0)
        data = output.read(65537)
    if result.returncode or len(data) > 65536:
        raise ValueError("Native SDK command failed (exit " + str(result.returncode)
                         + ") or exceeded diagnostic bounds:\n" + diagnostics(data[:8192], cwd))
    return data


def fixture(root, sdk, profile, extra_checks="", build_info_suffix=""):
    owned = root / "modules-\u03bc-\u0442\u0435\u0441\u0442"
    owned.mkdir()
    (owned / "sw_native_fixture.py").write_text('VALUE = "SyncWatch \\u03bc \\u0442\\u0435\\u0441\\u0442"\n', encoding="utf-8")
    search = [str(p) for p in (sdk["stdlib"], sdk["extensions"], owned) if p.is_dir() or (p.is_file() and p.suffix == ".zip")]
    application = root / "application/bin"
    application.mkdir(parents=True)
    executable = str(application / "owned-helper")
    values = dict.fromkeys(profile["startupPolicy"]["explicitPathFields"], str(sdk["prefix"]))
    for name in ("program_name", "executable", "base_executable"):
        values[name] = executable
    literal = lambda value: "L" + json.dumps(value, ensure_ascii=True)
    declarations = "\n".join("    ." + key + " = " + literal(value) + "," for key, value in values.items())
    inputs = {"EXPECTED_PATHS": search, "EXPECTED_EXECUTABLE": executable, "EXPECTED_PREFIX": str(sdk["prefix"]),
           "OWNED_ROOT": str(root)}
    checks = "\n".join(name + " = " + repr(value) for name, value in inputs.items()) + "\n"
    checks += (ROOT / "tools/python-runtime/native/checks.py").read_text(encoding="utf-8") + "\n" + extra_checks
    content = ("static const wchar_t *fixture_search[] = {" + ", ".join(map(literal, search)) + "};\n"
               "static const sw_python_paths fixture_paths = {\n" + declarations + "\n"
               f"    .search_path_count = {len(search)}, .search_paths = fixture_search\n}};\n"
               "static const wchar_t *fixture_missing = " + literal(str(root / "missing-stdlib")) + ";\n"
               "static const wchar_t *fixture_adjacent = " + literal(str(root / "owned-helper")) + ";\n"
               "static const wchar_t *fixture_runtime_wide = " + literal(str(sdk["runtime"])) + ";\n"
               "static const char *fixture_runtime = " + json.dumps(str(sdk["runtime"]), ensure_ascii=True) + ";\n"
               "static const char *fixture_build_info_suffix = " + json.dumps(build_info_suffix, ensure_ascii=True) + ";\n"
               "static const unsigned char fixture_check_bytes[] = {" +
               ",".join(str(byte) for byte in checks.encode("utf-8") + b"\0") + "};\n"
               "#define fixture_checks ((const char *)fixture_check_bytes)\n")
    (root / "fixture.h").write_text(content, encoding="utf-8", newline="\n")
    poison = root / "poison"
    poison.mkdir()
    for folder in (root, poison):
        for name in ("json", "sitecustomize", "usercustomize", "syncwatch_poison", "sw_native_fixture"):
            (folder / (name + ".py")).write_text('raise RuntimeError("Controlled poison executed")\n', encoding="utf-8")
    (root / "pyvenv.cfg").write_text("home = missing\ninclude-system-site-packages = true\n", encoding="utf-8")
    environment = {key: value for key, value in os.environ.items() if key.upper() in {"SYSTEMROOT", "WINDIR"}}
    environment.update(TEMP=str(root), TMP=str(root), PYTHONHOME=str(root / "missing-home"), PYTHONPATH=str(poison),
                       PYTHONUSERBASE=str(poison), PYTHONSTARTUP=str(poison / "json.py"), PYTHONINSPECT="1", PYTHONUTF8="0")
    if sys.platform == "win32":
        environment["PATH"] = str(sdk["runtime"].parent)
    return environment


def exercise_sdk(sdk, extra_checks="", selected_cases=CASES, on_output=None, case_timeout=30,
                 *, build_info_suffix=""):
    if not selected_cases or any(name not in CASES for name in selected_cases):
        raise ValueError("Unknown native startup verification case")
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    if GENERATED.read_text(encoding="utf-8") != render(profile):
        raise ValueError("Native policy differs from the shared startup profile")
    cmake, options, compiler = c_toolchain()
    with tempfile.TemporaryDirectory(prefix="sw-native-python-") as temporary:
        root = Path(temporary) / "startup-\u03bc-\u0442\u0435\u0441\u0442"
        root.mkdir()
        environment = fixture(root, sdk, profile, extra_checks, build_info_suffix)
        build = root / "build"
        execute([cmake, "-S", (ROOT / "tools/python-runtime/native").as_posix(), "-B", build.as_posix(), *options,
                 "-DCMAKE_BUILD_TYPE=Release", "-DSYNCWATCH_ROOT=" + ROOT.as_posix(), "-DSYNCWATCH_FIXTURE=" + root.as_posix(),
                 "-DSYNCWATCH_PYTHON_INCLUDE=" + sdk["include"].as_posix(), "-DSYNCWATCH_PYTHON_LIBRARY=" + sdk["library"].as_posix()], root)
        execute([cmake, "--build", str(build), "--config", "Release", "--parallel", "2"], root)
        binary = build / ("Release/startup-probe.exe" if sys.platform == "win32" else "startup-probe")
        cases = {}
        for name in selected_cases:
            try:
                data = execute([str(binary), name], root, environment, timeout=case_timeout)
            except ValueError as error:
                raise ValueError("Native startup case failed: " + name) from error
            if data.count(b"SYNCWATCH_NATIVE_STARTUP_OK") != 1:
                raise ValueError("Native startup case did not report success: " + name)
            if on_output is not None:
                on_output(name, data)
            cases[name] = True
        if list(root.rglob("*.pyc")) or list(root.rglob("__pycache__")):
            raise ValueError("Native startup wrote unexpected bytecode")
        return {"compiler": compiler, "cases": cases, "loadedLibraryVerified": True, "policySha256": sha(GENERATED)}


def probe():
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    if GENERATED.read_text(encoding="utf-8") != render(profile):
        raise ValueError("Native policy differs from the shared startup profile")
    sdk, identity = resolve()
    return {"developerSdkOnly": True, "sdk": identity, **exercise_sdk(sdk),
            "pinnedRuntimeBuilt": False, "pinnedEmbeddingAbiVerified": False, "runtimeReady": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = probe()
    data = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(data, encoding="utf-8")
    print(data, end="")


if __name__ == "__main__":
    main()
