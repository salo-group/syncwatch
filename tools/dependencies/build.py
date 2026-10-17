"""Validate, select, snapshot, build, and verify source-pinned dependencies."""

import argparse
import configparser
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[2]
PLATFORMS = {
    "linux-x64": ("Linux", {"x86_64", "AMD64"}, "ubuntu-24.04"),
    "macos-arm64": ("Darwin", {"arm64"}, "macos-15"),
    "windows-x64": ("Windows", {"AMD64", "x86_64"}, "windows-2025"),
}
OFFLINE = {
    "PIP_NO_INDEX": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    "npm_config_offline": "true", "CARGO_NET_OFFLINE": "true",
    "GIT_TERMINAL_PROMPT": "0", "GIT_NO_LAZY_FETCH": "1",
    "DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE": "true",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONNOUSERSITE": "1",
    **{name: "http://127.0.0.1:9" for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")},
    "NO_PROXY": "", "no_proxy": "",
}
FRAMEWORK_INPUTS = ("tools/dependencies", "tools/ci/artifact_upload.mjs", ".github/actions/dependency-build", ".github/workflows")
MAXIMUM_ARCHIVE = 256 * 1024**2


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "Duplicate manifest key")
            result[key] = value
        return result
    def invalid_number(_value):
        raise ValueError("Non-finite manifest number")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs, parse_constant=invalid_number)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def digest(path):
    with path.open("rb") as data:
        return hashlib.file_digest(data, "sha256").hexdigest()


def git(root, *arguments):
    return subprocess.check_output(["git", *arguments], cwd=root)


def member(value):
    require(isinstance(value, str) and bool(value), "Expected a relative manifest path")
    path = PurePosixPath(value)
    require(not path.is_absolute() and all(part not in {"..", "."} for part in path.parts), "Unsafe manifest path")
    require(str(path) == value and not re.search(r"[\\:*?\[\]\x00-\x1f]", value), "Unsafe manifest path")
    require(all(not part.endswith((".", " ")) and not re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part, re.I) for part in path.parts), "Path is not portable to Windows")
    return path


def inside(root, value):
    member(value)
    path = root / value
    require(path.resolve().is_relative_to(root.resolve()), "Path leaves its declared root")
    return path


def generated_path(root, value):
    path = inside(root, value)
    require(not any(parent.is_symlink() for parent in (path, *path.parents) if parent != root and parent.is_relative_to(root)), "Generated paths must not traverse symbolic links")
    return path


def index(root):
    entries = {}
    for item in git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if item:
            metadata, name = item.decode().split("\t", 1)
            mode, revision, stage = metadata.split()
            require(stage == "0", "Dependency builds require a resolved Git index")
            entries[name] = (mode, revision)
    return entries


def registry(root):
    data = read_json(root / "tools/dependencies/components.json")
    require(set(data) == {"schemaVersion", "components"} and type(data["schemaVersion"]) is int and data["schemaVersion"] == 1, "Unsupported dependency registry")
    components = data["components"]
    require(isinstance(components, dict) and components, "Dependency registry must be nonempty")
    entries = index(root)
    config = configparser.ConfigParser(interpolation=None)
    config.read(root / ".gitmodules", encoding="utf-8")
    modules = {config[section]["path"]: config[section]["url"] for section in config.sections()}
    for name, component in components.items():
        require(re.fullmatch(r"[a-z][a-z0-9-]{0,47}", name), "Invalid component name")
        require(isinstance(component, dict), "Invalid component description")
        if component.get("status") == "unavailable":
            require(set(component) == {"status", "reason", "plannedIn"} and bool(component["reason"]), "Unavailable component needs its reason and delivery")
            continue
        require(set(component) == {"status", "always", "roots", "sources", "inputs", "watch", "targets"} and component["status"] == "ready", "Ready component must declare its complete build inputs")
        require(type(component["always"]) is bool, "Invalid baseline selection")
        sources = component["sources"]
        require(isinstance(sources, dict) and sources, "Ready component requires pinned sources")
        for path, source in sources.items():
            require(re.fullmatch(r"3rdparty/[A-Za-z0-9_.-]+", path), "External sources must be top-level 3rdparty submodules")
            require(set(source) == {"revision", "license", "notices", "requires", "unusedSubmodules"}, "Invalid source graph entry")
            require(entries.get(path) == ("160000", source["revision"]), "Source revision must match its Git index pin")
            require(path in modules and re.fullmatch(r"https://[^/@?#\s]+/[^?#\s]+", modules[path]), "Source needs a credential-free HTTPS submodule registration")
            require(isinstance(source["license"], str) and source["license"], "Source license is required")
            require(isinstance(source["notices"], list) and source["notices"], "Source notices are required")
            for notice in source["notices"]:
                member(notice)
            require(isinstance(source["requires"], list) and all(item in sources for item in source["requires"]), "Source graph references an undeclared dependency")
            require(isinstance(source["unusedSubmodules"], dict), "Unused nested sources require explicit reasons")
            for nested, reason in source["unusedSubmodules"].items():
                member(nested)
                require(isinstance(reason, str) and reason, "Unused nested source needs its reason")
        require(isinstance(component["roots"], list) and component["roots"] and all(item in sources for item in component["roots"]), "Invalid source graph roots")
        reached, pending = set(), list(component["roots"])
        while pending:
            path = pending.pop()
            if path not in reached:
                reached.add(path)
                pending.extend(sources[path]["requires"])
        require(reached == set(sources), "Source graph contains unreachable entries")
        for field in ("inputs", "watch"):
            require(isinstance(component[field], list) and component[field], "Build inputs and watched paths must be explicit")
            for path in component[field]:
                inside(root, path)
        require(component["targets"] and set(component["targets"]).issubset(PLATFORMS), "Unsupported dependency target")
        for target in component["targets"].values():
            require(set(target) == {"toolchains", "environment", "flags", "command", "timeoutSeconds"}, "Invalid build target")
            require(isinstance(target["flags"], dict) and isinstance(target["environment"], dict), "Build flags and environment must be explicit")
            require(all(re.fullmatch(r"[A-Z][A-Z0-9_]*", key) and isinstance(value, str) for key, value in target["environment"].items()), "Invalid build environment")
            require(not (set(target["environment"]) & (set(OFFLINE) | {"PATH", "HOME", "PYTHONPATH"})), "Build environment cannot override toolchain or offline controls")
            require(type(target["timeoutSeconds"]) is int and 1 <= target["timeoutSeconds"] <= 14400, "Build command needs a bounded timeout")
            commands = [target["command"]]
            require(isinstance(target["toolchains"], dict) and target["toolchains"], "Toolchains must be declared")
            for tool in target["toolchains"].values():
                require(set(tool) == {"command", "pattern"} and tool["pattern"].startswith("^") and tool["pattern"].endswith("$"), "Toolchain compatibility must be explicit")
                re.compile(tool["pattern"])
                commands.append(tool["command"])
            for command in commands:
                require(isinstance(command, list) and command and all(isinstance(part, str) and part and "\x00" not in part for part in command), "Commands must be nonempty argument arrays")
    return components


def selected(components, changes, cold=False):
    infrastructure = changes is None or any(
        path == ".gitmodules" or any(path == prefix or path.startswith(prefix + "/") for prefix in (*FRAMEWORK_INPUTS, "tools/native", "global.json"))
        for path in (changes or [])
    )
    builds, unavailable = [], {}
    for name, component in components.items():
        if component["status"] == "unavailable":
            unavailable[name] = component["reason"]
            continue
        watched = [*component["watch"], *component["inputs"], *component["sources"]]
        affected = infrastructure or cold or component["always"] or any(
            path == prefix or path.startswith(prefix + "/") for path in (changes or []) for prefix in watched
        )
        if affected:
            builds.extend({"component": name, "platform": target, "cold": cold} for target in sorted(component["targets"]))
    return {"include": builds}, unavailable


def input_files(root, component):
    entries = index(root)
    result = {}
    for prefix in (*FRAMEWORK_INPUTS, *component["inputs"]):
        matched = [name for name, (mode, _) in entries.items() if mode != "160000" and (name == prefix or name.startswith(prefix + "/"))]
        require(matched, f"Build input has no tracked files: {prefix}")
        for name in matched:
            path = inside(root, name)
            require(path.is_file() and not path.is_symlink(), "Build inputs must be regular repository files")
            result[name] = {"sha256": digest(path), "mode": entries[name][0]}
    return result


def command(arguments):
    return [sys.executable if part == "{python}" else part for part in arguments]


def build_environment(specification):
    # Build recipes receive OS/tool discovery variables, not runner credentials or ambient flags.
    names = {"PATH", "HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL", "SYSTEMDRIVE", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432"}
    return {**{key: value for key, value in os.environ.items() if key.upper() in names}, **specification["environment"], **OFFLINE}


def initialize_sources(root, sources, directory):
    snapshots = {}
    config = configparser.ConfigParser(interpolation=None)
    config.read(root / ".gitmodules", encoding="utf-8")
    urls = {config[section]["path"]: config[section]["url"] for section in config.sections()}
    for path in sources:
        source = inside(root, path)
        if (source / ".git").exists():
            require(not git(source, "status", "--porcelain", "--untracked-files=normal").strip(), "Source checkout has local changes")
    subprocess.run(["git", "submodule", "sync", "--", *sources], cwd=root, check=True)
    subprocess.run(["git", "-c", "submodule.recurse=false", "-c", "fetch.recurseSubmodules=false", "submodule", "update", "--init", "--checkout", "--depth=1", "--", *sources], cwd=root, check=True)
    for path, specification in sources.items():
        source = root / path
        require(git(source, "rev-parse", "HEAD").decode().strip() == specification["revision"], "Initialized source has the wrong revision")
        require(not git(source, "status", "--porcelain", "--untracked-files=normal").strip(), "Initialized source must be clean")
        nested = configparser.ConfigParser(interpolation=None)
        nested.read(source / ".gitmodules", encoding="utf-8")
        nested_paths = {nested[section]["path"] for section in nested.sections()}
        # Consumed nested dependencies must be flattened into separately pinned 3rdparty entries.
        nested_links = {name: revision for name, (mode, revision) in index(source).items() if mode == "160000"}
        for name in nested_paths:
            require(name in specification["unusedSubmodules"] or nested_links.get(name) in {sources[item]["revision"] for item in specification["requires"]}, "Nested source is neither pinned separately nor explicitly unused")
        require(set(specification["unusedSubmodules"]).issubset(nested_paths), "Unused source declaration does not match upstream")
        for notice in specification["notices"]:
            require(git(source, "cat-file", "-t", f"HEAD:{notice}").strip() == b"blob", "Source notice is missing")
        archive = directory / (path.split("/")[-1] + ".tar")
        subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf", "archive", "--format=tar", "--output", str(archive), "HEAD"], cwd=source, check=True)
        snapshots[path] = {
            "url": urls[path], "revision": specification["revision"], "tree": git(source, "rev-parse", "HEAD^{tree}").decode().strip(),
            "archiveSha256": digest(archive),
        }
    return snapshots


def workspace(root, name, target):
    path = generated_path(root, f"artifacts/dependencies/{name}/{target}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def prepare(root, name, target):
    if os.environ.get("GITHUB_ACTIONS") == "true":
        subprocess.run(["git", "diff", "--exit-code", "HEAD", "--ignore-submodules=all"], cwd=root, check=True)
    components = registry(root)
    require(name in components and components[name]["status"] == "ready", "Component is unavailable")
    component = components[name]
    require(target in component["targets"], "Component has no build for this platform")
    system, architectures, _ = PLATFORMS[target]
    require(platform.system() == system and platform.machine() in architectures, "Dependency runner OS or architecture mismatch")
    directory = workspace(root, name, target)
    snapshots = initialize_sources(root, component["sources"], directory)
    specification = component["targets"][target]
    versions = {}
    environment = build_environment(specification)
    for tool, definition in specification["toolchains"].items():
        output = subprocess.check_output(command(definition["command"]), cwd=root, env=environment, stderr=subprocess.STDOUT, timeout=60).decode().strip()
        require(len(output) < 65536 and re.fullmatch(definition["pattern"], output), "Installed toolchain does not match its declared compatibility")
        versions[tool] = output
    inputs = {
        "schemaVersion": 1, "artifactSchema": 1, "component": name, "platform": target,
        "specification": component, "sources": snapshots, "files": input_files(root, component), "toolchains": versions,
        "host": {"system": platform.system(), "architecture": platform.machine(), "release": platform.release(),
                 "image": os.environ.get("ImageOS"), "imageVersion": os.environ.get("ImageVersion")},
        "offlineEnvironment": OFFLINE,
    }
    key = hashlib.sha256(canonical(inputs)).hexdigest()
    scope = "main" if os.environ.get("GITHUB_EVENT_NAME") in {"push", "schedule", "workflow_dispatch"} and os.environ.get("GITHUB_REF") == "refs/heads/main" else "local"
    if os.environ.get("GITHUB_EVENT_NAME") in {"pull_request", "merge_group"}:
        scope = "untrusted-" + os.environ["GITHUB_RUN_ID"]
    cache = generated_path(root, f".local/dependency-cache/{scope}/{key}.zip")
    request = {"key": key, "inputs": inputs, "archive": str(directory / "artifact.zip"), "cacheFile": str(cache)}
    (directory / "request.json").write_text(json.dumps(request, indent=2) + "\n", encoding="utf-8")
    return request


def unpack(archive, destination, expected):
    require(archive.stat().st_size <= MAXIMUM_ARCHIVE, "Cached archive exceeds its bound")
    with zipfile.ZipFile(archive) as package:
        entries = package.infolist()
        require(len(entries) <= 100000 and sum(item.file_size for item in entries) <= 1024**3, "Cached payload exceeds its bound")
        names = [item.filename for item in entries]
        require(len(names) == len(set(names)), "Duplicate cached archive member")
        require(len(names) == len({name.casefold() for name in names}), "Case-colliding cached archive members")
        for item in entries:
            member(item.filename)
            require(not item.is_dir() and item.external_attr >> 16 & 0o170000 in {0, 0o100000}, "Cached payload must contain regular files")
        manifest = json.loads(package.read("build-manifest.json"))
        require(isinstance(manifest, dict), "Invalid cached build manifest")
        require(manifest.get("key") == expected["key"] and canonical(manifest.get("inputs")) == canonical(expected["inputs"]), "Cached build inputs do not match")
        require(manifest.get("result") == "success" and manifest.get("schemaVersion") == 1, "Cached build did not succeed")
        files = manifest["files"]
        require(isinstance(files, dict), "Invalid cached payload inventory")
        require(set(names) == {*files, "build-manifest.json"} and bool(files), "Cached payload inventory does not match")
        for name, metadata in files.items():
            require(name.startswith("payload/") and metadata["mode"] in {0o644, 0o755}, "Invalid cached payload metadata")
            data = package.read(name)
            require(hashlib.sha256(data).hexdigest() == metadata["sha256"], "Cached payload checksum does not match")
        # Validate every input and byte before creating any output file.
        for name, metadata in files.items():
            path = inside(destination, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(package.read(name))
            path.chmod(metadata["mode"])
    return manifest


def pack(directory, manifest):
    payload = directory / "payload"
    require(not payload.is_symlink(), "Build payload must not be a symbolic link")
    files = {}
    total = 0
    for path in sorted(payload.rglob("*")):
        require(not path.is_symlink(), "Build output must not contain symbolic links")
        if path.is_file():
            name = path.relative_to(directory).as_posix()
            member(name)
            total += path.stat().st_size
            require(len(files) < 100000 and total <= 1024**3, "Built payload exceeds its bound")
            files[name] = {"sha256": digest(path), "mode": 0o755 if path.stat().st_mode & 0o111 else 0o644}
    require(len(files) == len({name.casefold() for name in files}), "Build output has case-colliding paths")
    manifest["files"] = files
    output = directory / "artifact.zip"
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, metadata in files.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (0o100000 | metadata["mode"]) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, (directory / name).read_bytes())
        archive.writestr("build-manifest.json", json.dumps(manifest, indent=2) + "\n")
    require(output.stat().st_size <= MAXIMUM_ARCHIVE, "Built archive exceeds its upload bound")
    return output


def build(root, name, target, cold=False):
    request = prepare(root, name, target)
    directory = workspace(root, name, target)
    payload = directory / "payload"
    if payload.exists():
        require(not payload.is_symlink(), "Build payload must not be a symbolic link")
        shutil.rmtree(payload)
    payload.mkdir()
    commit = git(root, "rev-parse", "HEAD").decode().strip()
    tree = git(root, "rev-parse", "HEAD^{tree}").decode().strip()
    dirty = bool(git(root, "status", "--porcelain", "--untracked-files=normal").strip())
    manifest = {
        "schemaVersion": 1, "key": request["key"], "inputs": request["inputs"], "result": "failure",
        "verifiedSourceCommit": commit, "buildSourceCommit": commit, "cacheHit": False,
        "verifiedSourceTree": tree, "buildSourceTree": tree,
        "verifiedLocalChanges": dirty, "buildLocalChanges": dirty,
        "runId": os.environ.get("GITHUB_RUN_ID"), "runAttempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
    }
    cache = Path(request["cacheFile"])
    specification = request["inputs"]["specification"]
    try:
        if not cold and cache.exists():
            cached = unpack(cache, directory, request)
            manifest.update(cacheHit=True, buildSourceCommit=cached["buildSourceCommit"], buildSourceTree=cached["buildSourceTree"], buildLocalChanges=cached["buildLocalChanges"])
        else:
            sources = directory / "sources"
            if sources.exists():
                shutil.rmtree(sources)
            sources.mkdir()
            for path in specification["sources"]:
                destination = sources / path
                destination.mkdir(parents=True)
                snapshot = directory / (path.split("/")[-1] + ".tar")
                require(digest(snapshot) == request["inputs"]["sources"][path]["archiveSha256"], "Source snapshot checksum does not match")
                with tarfile.open(snapshot) as archive:
                    archive.extractall(destination, filter="data")
            environment = build_environment(specification["targets"][target])
            environment.update({
                "SYNCWATCH_DEPENDENCY_OUTPUT": str(payload), "SYNCWATCH_DEPENDENCY_SOURCES": str(sources),
                "SYNCWATCH_DEPENDENCY_FLAGS": json.dumps(specification["targets"][target]["flags"]),
            })
            subprocess.run(command(specification["targets"][target]["command"]), cwd=root, env=environment, check=True, timeout=specification["targets"][target]["timeoutSeconds"])
            require(any(path.is_file() for path in payload.rglob("*")), "Build produced no payload")
            for path, source in specification["sources"].items():
                for notice in source["notices"]:
                    destination = payload / "notices" / path.split("/")[-1] / notice
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(inside(sources / path, notice), destination)
        require(input_files(root, specification) == request["inputs"]["files"], "Build changed its declared first-party inputs")
        for path, source in specification["sources"].items():
            require(git(root / path, "rev-parse", "HEAD").decode().strip() == source["revision"] and not git(root / path, "status", "--porcelain", "--untracked-files=normal").strip(), "Build changed its source checkout")
        if os.environ.get("GITHUB_ACTIONS") == "true":
            subprocess.run(["git", "diff", "--exit-code", "HEAD", "--ignore-submodules=all"], cwd=root, check=True)
        manifest["result"] = "success"
    finally:
        if manifest["result"] != "success":
            shutil.rmtree(payload)
            payload.mkdir()
        output = pack(directory, manifest)
    if not manifest["cacheHit"]:
        cache.parent.mkdir(parents=True, exist_ok=True)
        require(cache.resolve().is_relative_to((root / ".local/dependency-cache").resolve()), "Cache leaves its declared root")
        with tempfile.NamedTemporaryFile(dir=cache.parent, delete=False) as temporary:
            temporary.write(output.read_bytes())
            temporary_path = Path(temporary.name)
        temporary_path.replace(cache)
    print(f"Dependency {name}/{target}: {'verified cache hit' if manifest['cacheHit'] else 'cold build'}; key {request['key']}", flush=True)
    return manifest


def plan(root):
    components = registry(root)
    event = read_json(Path(os.environ["GITHUB_EVENT_PATH"])) if os.environ.get("GITHUB_EVENT_PATH") else {}
    kind = os.environ.get("GITHUB_EVENT_NAME")
    cold = kind == "schedule" or event.get("inputs", {}).get("cold") in {True, "true"}
    base = event.get("pull_request", {}).get("base", {}).get("sha") or event.get("before")
    changes = None
    if base and re.fullmatch(r"[0-9a-f]{40}", base) and set(base) != {"0"}:
        try:
            changes = git(root, "diff", "--no-renames", "--name-only", "-z", base, "HEAD").decode().strip("\0").split("\0")
        except subprocess.CalledProcessError:
            # An unavailable comparison selects every ready component; it never skips work.
            pass
    matrix, unavailable = selected(components, changes, cold)
    result = {"matrix": matrix, "unavailable": unavailable, "cold": cold}
    print(json.dumps(result, indent=2))
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write("matrix=" + json.dumps(matrix, separators=(",", ":")) + "\n")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
            summary.write("## Dependency build selection\n\n")
            for item in matrix["include"]:
                summary.write(f"- Selected: {item['component']} / {item['platform']}; cold={cold}\n")
            for name, reason in unavailable.items():
                summary.write(f"- Unavailable: {name}: {reason}\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("check", "plan", "prepare", "build"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--component", default=os.environ.get("CI_COMPONENT"))
    parser.add_argument("--platform", default=os.environ.get("CI_PLATFORM"), choices=PLATFORMS)
    parser.add_argument("--cold", action="store_true", default=os.environ.get("CI_COLD") == "true")
    args = parser.parse_args()
    try:
        root = args.root.resolve()
        if args.operation == "check":
            registry(root)
            print("Dependency manifests and source pins are valid")
        elif args.operation == "plan":
            plan(root)
        else:
            require(args.component and args.platform, "Component and platform are required")
            if args.operation == "prepare":
                prepare(root, args.component, args.platform)
            else:
                build(root, args.component, args.platform, args.cold)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, tarfile.TarError, zipfile.BadZipFile):
        print("Dependency validation/build failed; consult the failing stage output", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
