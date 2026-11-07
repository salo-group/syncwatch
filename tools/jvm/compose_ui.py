"""Own the complete desktop UI JVM source build; native rendering is a separate gate."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import zipfile

import compose_state
import compose_runtime
import concurrency
import jbr_api
import jdk
import kotlin
import modules
import stdlib
import stdlib_sources
from build_support import digest, encoded, jar_bytes

ROOT = Path(__file__).resolve().parents[2]
COMPONENT = "compose-ui-jvm"
CHANGES = (
    "SyncWatch desktop UI JVM source profile\n"
    "Selected upstream source files and their common/intermediate/JVM refinements are preserved.\n"
    "Skiko Version.kt is generated from the original pinned upstream generator template.\n"
    "Original compiler/build inputs and the generated file are retained with this profile.\n"
    "No Maven/Gradle binary, native renderer, fonts or platform library are build inputs.\n"
    "This owned profile is not an upstream Gradle artifact or an equivalence claim.\n"
    "Native rendering, application packaging and physical output need independent qualification.\n"
).encode()
OPT_INS = [
    "kotlin.ExperimentalMultiplatform", "kotlin.contracts.ExperimentalContracts",
    "kotlin.experimental.ExperimentalTypeInference", "androidx.compose.ui.ExperimentalComposeUiApi",
    "androidx.compose.ui.InternalComposeUiApi", "androidx.compose.ui.text.ExperimentalTextApi",
    "androidx.compose.foundation.ExperimentalFoundationApi", "androidx.compose.foundation.InternalFoundationApi",
    "androidx.compose.animation.ExperimentalAnimationApi", "androidx.compose.animation.core.InternalAnimationApi",
    "org.jetbrains.skiko.InternalSkikoApi", "kotlinx.coroutines.ExperimentalCoroutinesApi",
    "kotlinx.coroutines.InternalCoroutinesApi", "kotlinx.serialization.ExperimentalSerializationApi",
]
GENERATOR = "skiko/buildSrc/src/main/kotlin/tasks/configuration/CommonTasksConfiguration.kt"
DISPLAY = "skiko/src/awtMain/kotlin/org/jetbrains/skiko/Display.kt"
DISPLAY_RATE = b"getLinuxDisplayRefreshRate(it.display, it.window)"
DISPLAY_FALLBACK = (
    b"Skiko Display.kt configuration: retain valid Linux refresh rates; use the upstream "
    b"MinMainstreamMonitorRefreshRate when XRandR reports zero, negative, NaN or infinity.\n"
    b"This prevents the frame limiter from waiting indefinitely after display disconnection.\n"
    b"Original source bytes and the complete configured source accompany this artifact.\n"
)


def profile_data():
    profile = json.loads((ROOT / "tools/jvm/compose_ui.json").read_text())
    registry = json.loads((ROOT / "tools/dependencies/components.json").read_text())["components"]
    expected = registry[compose_state.COMPONENT]["sources"] | registry["jbr-api"]["sources"]
    selected = {name: [] for name in profile["sources"]}
    for module in profile["modules"]:
        selected[module["source"]].extend(path for paths_ in module["fragments"].values() for path in paths_)
    for name, source in profile["sources"].items():
        expected[source["path"]] = {key: source[key] for key in ("revision", "license", "notices", "requires", "exports")} | {"unusedSubmodules": {}}
        code = {path for path in source["files"] if path.endswith((".kt", ".java"))} - set(source.get("buildSources", []))
        if (set(source["exports"]) != set(source["files"]) or not set(source["notices"]) <= source["files"].keys()
                or len(selected[name]) != len(set(selected[name])) or set(selected[name]) != code):
            raise ValueError("Desktop UI modules must cover the exact source and notice inventory once")
    if (profile["schemaVersion"] != 1 or profile["profile"] != "desktop-ui-jvm11-v1"
            or profile.get("displayRefreshFallback") is not True
            or profile["compilerVersion"] != kotlin.specification()["version"]
            or registry[COMPONENT]["sources"] != expected):
        raise ValueError("Desktop UI source/compiler graph differs from the reviewed profile")
    return profile


def generated_version(files, profile):
    generator = files["skiko"][GENERATOR].decode()
    matches = re.findall(r'File\(out\)\.writeText\(\s*"""(.*?)"""\.trimIndent', generator, re.S)
    if len(matches) != 1:
        raise ValueError("Review the original Skiko version generator before changing the profile")
    text = textwrap.dedent(matches[0]).strip("\n")
    text = text.replace("${skikoProperties.deployVersion}", profile["version"]["skiko"])
    text = text.replace("$skiaTag", profile["version"]["skiaTag"])
    if "$" in text:
        raise ValueError("Unresolved Skiko version generator parameter")
    return text.encode()


def configured_sources(files, profile):
    if not profile.get("displayRefreshFallback"):
        return {}
    original = files["skiko"][DISPLAY]
    if (digest(original) != profile["sources"]["skiko"]["files"][DISPLAY]
            or original.count(DISPLAY_RATE) != 1):
        raise ValueError("Review the original Skiko display refresh source before configuration")
    configured = original.replace(DISPLAY_RATE, b"normalizeDisplayRefreshRate(" + DISPLAY_RATE + b")")
    configured += (b"\n// SyncWatch configuration: disconnected/virtual outputs may have no usable refresh rate.\n"
                   b"private fun normalizeDisplayRefreshRate(rate: Double): Double =\n"
                   b"    rate.takeIf { it.isFinite() && it > 0.0 } ?: MinMainstreamMonitorRefreshRate\n")
    return {"skiko/" + DISPLAY: configured}


def support(files, profile):
    return ({"profile.json": encoded(profile), "CHANGES.txt": CHANGES,
             "generated/Version.kt": generated_version(files, profile)}
            | ({"CONFIGURATION.txt": DISPLAY_FALLBACK} if profile.get("displayRefreshFallback") else {})
            | {"configured/" + name: data for name, data in configured_sources(files, profile).items()}
            | {"originals/" + name + "/" + path: data
               for name, values in files.items() for path, data in values.items()})


def paths(payload, profile):
    return [*compose_state.paths(payload / "parent", compose_state.profile_data()),
            payload / "jbr-api" / jbr_api.BINARY, *[payload / module["binary"] for module in profile["modules"]]]


def assemble(files, profile, payload, home, compiler):
    classpath = [*compose_state.paths(payload / "parent", compose_state.profile_data()),
                 payload / "jbr-api" / jbr_api.BINARY]
    with tempfile.TemporaryDirectory(prefix="compose-ui-build-") as temporary:
        work = Path(temporary).resolve()
        for name, values in files.items():
            for filename, data in values.items():
                path = work / name / filename
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        for name, data in configured_sources(files, profile).items():
            (work / name).write_bytes(data)
        version = work / "generated/Version.kt"
        version.parent.mkdir()
        version.write_bytes(generated_version(files, profile))
        for module in profile["modules"]:
            name, source = module["name"], module["source"]
            classes = work / (name + "-classes")
            classes.mkdir()
            fragments = {role: [work / source / path for path in names] for role, names in module["fragments"].items()}
            if source == "skiko":
                fragments["awt"].append(version)
            language = module["languageVersion"]
            flags = ["-jvm-target", module["jvmTarget"], "-language-version", language, "-api-version", language,
                     "-module-name", name, "-jvm-default=enable", "-Xdont-warn-on-error-suppression",
                     *["-opt-in=" + marker for marker in OPT_INS]]
            modules.compile_module(fragments, module["refines"], classpath, classes, work, home, compiler,
                                   flags, allow_empty_platform=source != "skiko", plugins=module["plugins"])
            java = [path for names in fragments.values() for path in names if path.suffix == ".java"]
            if java:
                stdlib.javac(["--release", "11", "-g:none", "-proc:none", "-encoding", "UTF-8", "-cp",
                              os.pathsep.join(map(str, [classes, *classpath])), "-d", str(classes), *map(str, java)], work, home)
            entries = {path.relative_to(classes).as_posix(): path.read_bytes() for path in classes.rglob("*") if path.is_file()}
            changes = CHANGES + (DISPLAY_FALLBACK if source == "skiko" and profile.get("displayRefreshFallback") else b"")
            entries |= {"META-INF/CHANGES.txt": changes,
                        "META-INF/UPSTREAM-NOTICES.txt": concurrency.source_notices(files, profile, source)}
            destination = payload / module["binary"]
            destination.write_bytes(jar_bytes(entries))
            classpath.append(destination)
            shutil.rmtree(classes)
            print("Compiled " + name, flush=True)


def verify(payload, files, profile, notices):
    names = {module["binary"] for module in profile["modules"]}
    actual = set()
    for path in payload.rglob("*"):
        if path.is_symlink():
            raise ValueError("Desktop UI payload contains a symbolic link")
        if path.is_file() and path.relative_to(payload).parts[0] not in {"parent", "jbr-api"}:
            actual.add(path.relative_to(payload).as_posix())
    if actual != names | {"compose-ui-sources.jar"} | notices.keys():
        raise ValueError("Desktop UI payload has missing or unexpected files")
    if any((payload / name).read_bytes() != data for name, data in notices.items()):
        raise ValueError("Desktop UI original source notice changed")
    with zipfile.ZipFile(payload / "compose-ui-sources.jar") as archive:
        expected = support(files, profile)
        if (len(archive.namelist()) != len(expected) or set(archive.namelist()) != set(expected)
                or any(archive.read(name) != data for name, data in expected.items())):
            raise ValueError("Desktop UI original sources, notices, generation or profile changed")
    pins = profile["expectedJars"].get(jdk.current_target(), {})
    if set(pins) != names:
        raise ValueError("Desktop UI requires exact target pins for every runtime artifact")
    for name, expected in pins.items():
        if digest((payload / name).read_bytes()) != expected:
            raise ValueError("Desktop UI artifact differs from the reviewed compiler output: " + name)
    print("Verified sixteen source-built desktop UI modules and original source/notice/generator correspondence")


def probe(payload, profile, home, compiler):
    if sys.platform != "linux":
        raise SystemExit("Linux application/design consumer requires Linux")
    # Application changes are verified freshly without changing the vendor artifact identity.
    import importlib.util
    source = ROOT / "tools/consumers/linux_application.py"
    specification = importlib.util.spec_from_file_location("syncwatch_linux_application", source)
    consumer = importlib.util.module_from_spec(specification)
    previous_path = sys.path[:]
    try:
        sys.path.insert(0, str(source.parent))
        specification.loader.exec_module(consumer)
    finally:
        sys.path[:] = previous_path
    consumer.check(ROOT, paths(payload.resolve(), profile), home, compiler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("build", "verify"))
    parser.add_argument("--platform", choices=("linux-x64", "macos-arm64", "windows-x64"))
    args = parser.parse_args()
    profile = profile_data()
    home, compiler = jdk.home(), kotlin.home()
    archive = args.operation == "verify"
    directory = ROOT / "artifacts/dependencies" / COMPONENT / (args.platform or jdk.current_target()) if archive else Path(os.environ["SYNCWATCH_DEPENDENCY_SOURCES"])
    files = concurrency.read_sources(directory, profile, archive=archive)
    for name, source in profile["sources"].items():
        unheaded = {path for path, data in files[name].items() if path.endswith((".kt", ".java")) and stdlib_sources.apache_header(data) is None}
        if unheaded != set(source["repositoryLicensedFiles"]):
            raise ValueError("Desktop UI source headers differ from the reviewed repository grant")
    parent_files = concurrency.read_sources(directory, compose_state.profile_data(), archive=archive)
    parent_sources = compose_runtime.original_sources(directory, archive=archive)
    jbr_profile, sdk = jbr_api.profile_data()
    jbr_files = jbr_api.source_files(directory / ("jbr-api.tar" if archive else "3rdparty/jbr-api"), jbr_profile)
    notices = compose_runtime.original_notices(COMPONENT, parent_sources | parent_files | files | {"jbr-api": jbr_files})
    if not archive:
        if json.loads(os.environ["SYNCWATCH_DEPENDENCY_FLAGS"]) != {"profile": profile["profile"]}:
            raise ValueError("Unknown desktop UI source profile")
        payload = Path(os.environ["SYNCWATCH_DEPENDENCY_OUTPUT"])
        for script, folder, flags in [("compose_state.py", "parent", {"profile": compose_state.profile_data()["profile"]}),
                                      ("jbr_api.py", "jbr-api", {"profile": "java8-module9-v1", "version": "1.9.0"})]:
            destination = payload / folder
            destination.mkdir()
            environment = dict(os.environ, SYNCWATCH_DEPENDENCY_OUTPUT=str(destination), SYNCWATCH_DEPENDENCY_FLAGS=json.dumps(flags))
            subprocess.run([sys.executable, str(ROOT / "tools/jvm" / script), "build"], env=environment, check=True)
        assemble(files, profile, payload, home, compiler)
        (payload / "compose-ui-sources.jar").write_bytes(jar_bytes(support(files, profile)))
        for name, data in notices.items():
            path = payload / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    else:
        payload = directory / "payload"
    # Parent verifiers retain their independent source/compiler and exact-payload checks.
    compose_runtime.verify_runtime(payload / "parent/parent", parent_sources)
    compose_state.verify(payload / "parent", parent_files, compose_state.profile_data(),
                         compose_runtime.original_notices(compose_state.COMPONENT, parent_sources | parent_files))
    jbr_api.verify(payload / "jbr-api", jbr_files, jbr_profile, sdk["version"])
    verify(payload, files, profile, notices)
    if archive:
        probe(payload, profile, home, compiler)


if __name__ == "__main__":
    main()
