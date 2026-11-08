"""Validate, select, snapshot, build, and verify source-pinned dependencies."""

import argparse
import configparser
from contextlib import contextmanager
import hashlib
import io
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

from git_objects import read_blobs
from git_transport import run as run_git


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
FRAMEWORK_INPUTS = ("tools/dependencies", "tools/ci/artifact_upload.mjs", "tools/ci/artifact_download.mjs",
                    "tools/ci/artifact_policy.mjs", "tools/ci/artifact-policy.json",
                    ".github/actions/dependency-build", ".github/workflows")
MAXIMUM_ARCHIVE = 256 * 1024**2
MAXIMUM_DOCUMENTATION_PATHS = 512


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
    # Checkout already enables this option. Reads must use the same filesystem
    # semantics or Git for Windows reports unchanged long source paths as modified.
    return run_git(["-c", "core.longpaths=true", *arguments], cwd=root, capture_output=True).stdout


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


def export_paths(source):
    if "exports" not in source:
        return None
    paths = source["exports"]
    require(isinstance(paths, list) and 0 < len(paths) <= 4096, "Source exports must be a nonempty bounded list")
    for path in paths:
        member(path)
    require(paths == sorted(set(paths)), "Source exports must be sorted and unique")
    folded = [path.casefold() for path in paths]
    require(len(set(folded)) == len(folded) and not any(
        a != b and b.startswith(a + "/") for a in folded for b in folded
    ), "Source exports must not overlap or collide across platforms")
    require(all(any(notice == path or notice.startswith(path + "/") for path in paths) for notice in source["notices"]),
            "Source exports must include every declared license/notice")
    return paths


def export_pathspec_batches(paths):
    """Keep explicit inventories below Windows' UTF-16 command-line limit."""
    batch, units = [], 0
    for path in paths:
        spec = ":(literal)" + path
        cost = len(spec.encode("utf-16-le")) // 2 + 3
        require(cost <= 8192, "Source export path exceeds the portable Git argument bound")
        if batch and units + cost > 8192:
            yield batch
            batch, units = [], 0
        batch.append(spec)
        units += cost
    if batch:
        yield batch


def exported_entries(source, paths):
    entries = {}
    for batch in export_pathspec_batches(paths):
        for item in git(source, "ls-tree", "-rz", "--full-tree", "HEAD", "--", *batch).split(b"\0"):
            if item:
                metadata, name = item.decode("utf-8").split("\t", 1)
                mode, kind, revision = metadata.split()
                if kind == "blob":
                    member(name)
                    require(mode in {"100644", "100755", "120000"}, "Unsupported source export mode")
                    entries[name] = (mode, revision)
    require(all(any(name == path or name.startswith(path + "/") for name in entries) for path in paths),
            "Source export is missing or has no file content at the pinned revision")
    portable = {}
    for name in entries:
        for path in (PurePosixPath(name), *PurePosixPath(name).parents):
            spelling = str(path)
            previous = portable.setdefault(spelling.casefold(), spelling)
            require(previous == spelling, "Exported source paths collide across platforms")
    return entries


def verify_source_export(archive, entries, revision, notices):
    with tarfile.open(archive) as package:
        require(package.pax_headers.get("comment") == revision, "Source export lost its pinned Git revision")
        found = set()
        for entry in package:
            if entry.isdir():
                continue
            name = entry.name
            require(name in entries and name not in found, "Source export contains unexpected or duplicate files")
            found.add(name)
            mode, expected = entries[name]
            require(entry.issym() if mode == "120000" else entry.isfile(), "Source export changed a Git file type")
            if entry.isfile():
                require(bool(entry.mode & 0o111) == (mode == "100755"), "Source export changed a Git executable bit")
            require(name not in notices or entry.isfile(), "Source notice must be a regular exported file")
            size = len(entry.linkname.encode("utf-8")) if entry.issym() else entry.size
            checksum = hashlib.new("sha1" if len(expected) == 40 else "sha256", b"blob " + str(size).encode() + b"\0")
            if entry.issym():
                checksum.update(entry.linkname.encode("utf-8"))
            else:
                with package.extractfile(entry) as data:
                    while chunk := data.read(1024 * 1024):
                        checksum.update(chunk)
            require(checksum.hexdigest() == expected, "Source export changed pinned Git blob bytes")
        require(found == set(entries), "Source export omitted pinned files (check export-ignore attributes)")


def export_original_blobs(source, archive, entries, revision):
    """Archive exact Git blobs, without checkout/archive EOL or export filters."""
    objects = list(dict.fromkeys(identity for _mode, identity in entries.values()))
    data = read_blobs(source, objects)
    stream = io.BytesIO(data)
    blobs = {}
    for identity in objects:
        header = stream.readline().decode("ascii").strip().split()
        require(len(header) == 3 and header[:2] == [identity, "blob"], "Git object export returned an unexpected type")
        contents = stream.read(int(header[2]))
        require(len(contents) == int(header[2]) and stream.read(1) == b"\n", "Truncated Git blob export")
        blobs[identity] = contents
    require(not stream.read(1), "Unexpected Git object export data")
    with tarfile.open(archive, "w", format=tarfile.PAX_FORMAT, pax_headers={"comment": revision}) as package:
        for name, (mode, identity) in sorted(entries.items()):
            contents = blobs[identity]
            entry = tarfile.TarInfo(name)
            entry.mode = int(mode[-3:], 8)
            if mode == "120000":
                entry.type = tarfile.SYMTYPE
                entry.linkname = contents.decode("utf-8")
                package.addfile(entry)
            else:
                entry.size = len(contents)
                package.addfile(entry, io.BytesIO(contents))


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
        required = {"status", "always", "roots", "sources", "inputs", "watch", "targets"}
        require(set(component) in (required, required | {"verificationInputs"}) and component["status"] == "ready", "Ready component must declare its complete build inputs")
        require(type(component["always"]) is bool, "Invalid baseline selection")
        sources = component["sources"]
        require(isinstance(sources, dict) and sources, "Ready component requires pinned sources")
        for path, source in sources.items():
            require(re.fullmatch(r"3rdparty/[A-Za-z0-9_.-]+", path), "External sources must be top-level 3rdparty submodules")
            required = {"revision", "license", "notices", "requires", "unusedSubmodules"}
            require(set(source) in (required, required | {"exports"}, required | {"exports", "checkoutDirectories"}),
                    "Invalid source graph entry")
            require(entries.get(path) == ("160000", source["revision"]), "Source revision must match its Git index pin")
            require(path in modules and re.fullmatch(r"https://[^/@?#\s]+/[^?#\s]+", modules[path]), "Source needs a credential-free HTTPS submodule registration")
            require(isinstance(source["license"], str) and source["license"], "Source license is required")
            require(isinstance(source["notices"], list) and source["notices"], "Source notices are required")
            for notice in source["notices"]:
                member(notice)
            export_paths(source)
            if "checkoutDirectories" in source:
                directories = source["checkoutDirectories"]
                require(isinstance(directories, list) and directories, "Sparse index checkout requires directories")
                for directory in directories:
                    member(directory)
                require(directories == sorted(set(directories)), "Sparse index checkout requires unique sorted directories")
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
        for field in ("inputs", "watch", *(["verificationInputs"] if "verificationInputs" in component else [])):
            require(isinstance(component[field], list) and component[field], "Build inputs and watched paths must be explicit")
            for path in component[field]:
                inside(root, path)
        for path in component.get("verificationInputs", []):
            require(not any(path == prefix or path.startswith(prefix + "/") or prefix.startswith(path + "/")
                            for prefix in (*FRAMEWORK_INPUTS, *component["inputs"])),
                    "Build and verification inputs must not overlap")
        require(component["targets"] and set(component["targets"]).issubset(PLATFORMS), "Unsupported dependency target")
        for target in component["targets"].values():
            require(set(target) in ({"toolchains", "environment", "flags", "command", "timeoutSeconds"},
                                   {"toolchains", "environment", "flags", "command", "timeoutSeconds", "verifyCommand"}), "Invalid build target")
            require(not component.get("verificationInputs") or "verifyCommand" in target,
                    "Verification inputs require a fresh verifier on every target")
            require(isinstance(target["flags"], dict) and isinstance(target["environment"], dict), "Build flags and environment must be explicit")
            require(all(re.fullmatch(r"[A-Z][A-Z0-9_]*", key) and isinstance(value, str) for key, value in target["environment"].items()), "Invalid build environment")
            require(not (set(target["environment"]) & (set(OFFLINE) | {"PATH", "HOME", "PYTHONPATH"})), "Build environment cannot override toolchain or offline controls")
            require(type(target["timeoutSeconds"]) is int and 1 <= target["timeoutSeconds"] <= 14400, "Build command needs a bounded timeout")
            commands = [target["command"]]
            if "verifyCommand" in target:
                commands.append(target["verifyCommand"])
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
        watched = [*component["watch"], *component["inputs"], *component.get("verificationInputs", []), *component["sources"]]
        affected = infrastructure or cold or component["always"] or any(
            path == prefix or path.startswith(prefix + "/") for path in (changes or []) for prefix in watched
        )
        if affected:
            builds.extend({"component": name, "platform": target, "cold": cold} for target in sorted(component["targets"]))
    return {"include": builds}, unavailable


def input_files(root, component, *, verification=False):
    prefixes = component.get("verificationInputs", []) if verification else (*FRAMEWORK_INPUTS, *component["inputs"])
    if not prefixes:
        return {}
    entries = index(root)
    if verification:
        # Local checks may include new application sources before they are staged.
        for name in git(root, "ls-files", "--others", "--exclude-standard", "-z").decode().split("\0"):
            if name and any(name == prefix or name.startswith(prefix + "/") for prefix in prefixes):
                path = inside(root, name)
                entries[name] = ("100755" if os.access(path, os.X_OK) else "100644", "")
    result = {}
    for prefix in prefixes:
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
    names = {"PATH", "HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL", "SYSTEMDRIVE", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "SYNCWATCH_AUTOTOOLS_BIN",
             "SYNCWATCH_VLC_MSYS_ROOT", "SYNCWATCH_VLC_EXTRA_TOOLS_ROOT", "SYNCWATCH_VLC_AUTOTOOLS_ROOT"}
    return {**{key: value for key, value in os.environ.items() if key.upper() in names}, **specification["environment"], **OFFLINE}


def initialize_export_source(root, path, specification, url):
    """Apply reviewed paths before the first checkout of a large upstream tree."""
    source = inside(root, path)
    created = not (source / ".git").exists()
    if created:
        require(not source.exists() or (source.is_dir() and not any(source.iterdir())), "Uninitialized source path must be empty")
        run_git(["clone", "--no-checkout", "--filter=blob:none", "--depth=1", "--no-tags", "--", url, str(source)], cwd=root)
        subprocess.run(["git", "submodule", "absorbgitdirs", "--", path], cwd=root, check=True)
    revision = specification["revision"]
    # Object-existence probes can trigger an unbounded promisor fetch of missing history.
    if created or git(source, "rev-parse", "HEAD").decode().strip() != revision:
        run_git(["-c", "fetch.recurseSubmodules=false", "fetch", "--filter=blob:none", "--depth=1", "--no-tags", "origin", revision], cwd=source)
    if created:
        # No worktree/index exists yet: detach at the exact pin before materializing files.
        subprocess.run(["git", "update-ref", "--no-deref", "HEAD", revision], cwd=source, check=True)
    directories = specification.get("checkoutDirectories")
    if directories:
        for directory in directories:
            require(git(source, "cat-file", "-t", f"HEAD:{directory}").strip() == b"tree",
                    "Sparse index checkout requires original directories")
        patterns = "\n".join(directories) + "\n"
        mode = ["--cone", "--sparse-index"]
    else:
        patterns = "".join("/" + name + "\n" for name in export_paths(specification))
        mode = ["--no-cone"]
    run_git(["-c", "core.longpaths=true", "-c", "core.autocrlf=false", "-c", "core.eol=lf",
             "sparse-checkout", "set", *mode, "--stdin"], cwd=source, input=patterns)
    run_git(["-c", "core.longpaths=true", "-c", "core.autocrlf=false", "-c", "core.eol=lf",
             "checkout", "--detach", revision], cwd=source)


def initialize_sources(root, sources, directory, *, reuse_prepared_sources=False):
    snapshots = {}
    config = configparser.ConfigParser(interpolation=None)
    config.read(root / ".gitmodules", encoding="utf-8")
    urls = {config[section]["path"]: config[section]["url"] for section in config.sections()}
    for path in sources:
        source = inside(root, path)
        if (source / ".git").exists():
            require(not git(source, "status", "--porcelain", "--untracked-files=normal").strip(), "Source checkout has local changes")
    subprocess.run(["git", "submodule", "sync", "--", *sources], cwd=root, check=True)
    subprocess.run(["git", "submodule", "init", "--", *sources], cwd=root, check=True)
    whole = [path for path, specification in sources.items() if export_paths(specification) is None]
    if whole:
        run_git(["-c", "submodule.recurse=false", "-c", "fetch.recurseSubmodules=false", "submodule", "update", "--init", "--checkout", "--depth=1", "--", *whole], cwd=root)
    for path, specification in sources.items():
        if export_paths(specification) is not None:
            initialize_export_source(root, path, specification, urls[path])
    for path, specification in sources.items():
        source = root / path
        require(git(source, "rev-parse", "HEAD").decode().strip() == specification["revision"], "Initialized source has the wrong revision")
        require(not git(source, "status", "--porcelain", "--untracked-files=normal").strip(), "Initialized source must be clean")
        nested = configparser.ConfigParser(interpolation=None)
        if subprocess.run(["git", "cat-file", "-e", "HEAD:.gitmodules"], cwd=source, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            nested.read_string(git(source, "show", "HEAD:.gitmodules").decode("utf-8"))
        nested_paths = {nested[section]["path"] for section in nested.sections()}
        # Consumed nested dependencies must be flattened into separately pinned 3rdparty entries.
        # Inspect the immutable tree without expanding a protected sparse index.
        # ls-files expands excluded upstream names that are invalid on Windows.
        nested_links = {}
        for entry in git(source, "ls-tree", "-rz", "--full-tree", "HEAD").split(b"\0"):
            if entry:
                metadata, name = entry.decode("utf-8").split("\t", 1)
                mode, kind, revision = metadata.split()
                if mode == "160000" and kind == "commit":
                    nested_links[name] = revision
        for name in nested_paths:
            require(name in specification["unusedSubmodules"] or nested_links.get(name) in {sources[item]["revision"] for item in specification["requires"]}, "Nested source is neither pinned separately nor explicitly unused")
        require(set(specification["unusedSubmodules"]).issubset(nested_paths), "Unused source declaration does not match upstream")
        for notice in specification["notices"]:
            require(git(source, "cat-file", "-t", f"HEAD:{notice}").strip() == b"blob", "Source notice is missing")
        archive = directory / (path.split("/")[-1] + ".tar")
        exports = export_paths(specification)
        entries = exported_entries(source, exports) if exports is not None else None
        arguments = ["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf", "archive", "--format=tar", "--output", str(archive), "HEAD"]
        if exports is not None:
            arguments += ["--", *[":(literal)" + p for p in exports]]
        reused = reuse_prepared_sources and entries is not None and archive.exists()
        if reused:
            require(archive.is_file() and not archive.is_symlink(), "Prepared source archive must be a regular file")
        elif entries is None:
            subprocess.run(arguments, cwd=source, check=True)
        else:
            export_original_blobs(source, archive, entries, specification["revision"])
        if exports is not None:
            verify_source_export(archive, entries, specification["revision"], specification["notices"])
        if reused:
            print(f"Reused original source export after fresh Git blob verification: {path}", flush=True)
        snapshots[path] = {
            "url": urls[path], "revision": specification["revision"], "tree": git(source, "rev-parse", "HEAD^{tree}").decode().strip(),
            "archiveSha256": digest(archive),
        }
        if exports is not None:
            snapshots[path]["exports"] = exports
    return snapshots


def workspace(root, name, target):
    path = generated_path(root, f"artifacts/dependencies/{name}/{target}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def prepare(root, name, target, *, reuse_prepared_sources=False):
    if os.environ.get("GITHUB_ACTIONS") == "true":
        subprocess.run(["git", "diff", "--exit-code", "HEAD", "--ignore-submodules=all"], cwd=root, check=True)
    components = registry(root)
    require(name in components and components[name]["status"] == "ready", "Component is unavailable")
    component = components[name]
    require(target in component["targets"], "Component has no build for this platform")
    system, architectures, _ = PLATFORMS[target]
    require(platform.system() == system and platform.machine() in architectures, "Dependency runner OS or architecture mismatch")
    directory = workspace(root, name, target)
    snapshots = initialize_sources(root, component["sources"], directory,
                                   reuse_prepared_sources=reuse_prepared_sources)
    specification = component["targets"][target]
    versions = {}
    environment = build_environment(specification)
    for tool, definition in specification["toolchains"].items():
        try:
            output = subprocess.check_output(command(definition["command"]), cwd=root, env=environment, stderr=subprocess.STDOUT, timeout=60).decode().strip()
        except subprocess.CalledProcessError as error:
            print(f"Developer SDK probe failed: {name}/{target}/{tool}", file=sys.stderr)
            print(error.output.decode(errors="replace")[-8192:], file=sys.stderr)
            raise
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
    request = {"key": key, "inputs": inputs, "archive": str(directory / "artifact.zip"), "cacheFile": str(cache),
               "verificationInputs": input_files(root, component, verification=True)}
    (directory / "request.json").write_text(json.dumps(request, indent=2) + "\n", encoding="utf-8")
    return request


@contextmanager
def checked_archive(archive):
    """Apply the same portable ZIP boundary to SDKs and native runtime inputs."""
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
        yield package


def extract_payload(package, destination, files, *, metadata_files=()):
    """Verify every declared payload before writing, using bounded stream buffers."""
    require(isinstance(files, dict) and bool(files), "Invalid cached payload inventory")
    require(set(package.namelist()) == {*files, *metadata_files}, "Cached payload inventory does not match")
    for name, metadata in files.items():
        require(name.startswith("payload/") and metadata["mode"] in {0o644, 0o755}, "Invalid cached payload metadata")
        with package.open(name) as source:
            require(hashlib.file_digest(source, "sha256").hexdigest() == metadata["sha256"], "Cached payload checksum does not match")
    # Consumers reserve an empty stage; dependency producers own their SDK workspace.
    for name, metadata in files.items():
        path = inside(destination, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with package.open(name) as source, path.open("wb") as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)
        path.chmod(metadata["mode"])


def unpack(archive, destination, expected):
    with checked_archive(archive) as package:
        manifest = json.loads(package.read("build-manifest.json"))
        require(isinstance(manifest, dict), "Invalid cached build manifest")
        require(manifest.get("key") == expected["key"] and canonical(manifest.get("inputs")) == canonical(expected["inputs"]), "Cached build inputs do not match")
        require(manifest.get("result") == "success" and manifest.get("schemaVersion") == 1, "Cached build did not succeed")
        extract_payload(package, destination, manifest["files"], metadata_files=("build-manifest.json",))
    return manifest


def payload_inventory(directory):
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
    return files


def pack(directory, manifest):
    files = payload_inventory(directory)
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


def build(root, name, target, cold=False, *, reuse_prepared_sources=False):
    request = prepare(root, name, target, reuse_prepared_sources=reuse_prepared_sources and not cold)
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
        "verificationInputs": request["verificationInputs"],
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
        target_specification = specification["targets"][target]
        original_payload = payload_inventory(directory) if request["verificationInputs"] else None
        if "verifyCommand" in target_specification:
            subprocess.run(command(target_specification["verifyCommand"]), cwd=root,
                           env=build_environment(target_specification), check=True,
                           timeout=target_specification["timeoutSeconds"])
        if original_payload is not None:
            require(payload_inventory(directory) == original_payload, "Verification changed the SDK payload")
        require(input_files(root, specification) == request["inputs"]["files"], "Build changed its declared first-party inputs")
        require(input_files(root, specification, verification=True) == request["verificationInputs"],
                "Verification changed its declared application inputs")
        for path, source in specification["sources"].items():
            require(git(root / path, "rev-parse", "HEAD").decode().strip() == source["revision"] and not git(root / path, "status", "--porcelain", "--untracked-files=normal").strip(), "Build changed its source checkout")
        if os.environ.get("GITHUB_ACTIONS") == "true":
            subprocess.run(["git", "diff", "--exit-code", "HEAD", "--ignore-submodules=all"], cwd=root, check=True)
        manifest["result"] = "success"
    finally:
        if manifest["result"] != "success":
            if payload.is_symlink() or (payload.exists() and not payload.is_dir()):
                payload.unlink()
            elif payload.exists():
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


def documentation_change(root, base, kind, changes, cold, components):
    eligible_event = kind == "pull_request" or (kind == "push" and os.environ.get("GITHUB_REF") == "refs/heads/main")
    if cold or not eligible_event or not isinstance(changes, list) or not 0 < len(changes) <= MAXIMUM_DOCUMENTATION_PATHS:
        return False
    for path in changes:
        try:
            member(path)
        except ValueError:
            return False
        if any(character in path for character in '<>"|'):
            return False
        if path != "README.md" and not (path.startswith("docs/") and path.endswith(".md")):
            return False
        for component in components.values():
            if component["status"] != "ready":
                continue
            declared = [*component["watch"], *component["inputs"], *component.get("verificationInputs", []), *component["sources"]]
            if any(path.casefold() == prefix.casefold() or path.casefold().startswith(prefix.casefold() + "/") for prefix in declared):
                return False
    seen = set()
    try:
        for revision in (base, "HEAD"):
            entries = git(root, "ls-tree", "-r", "-z", revision, "--", *(":(literal)" + path for path in changes))
            for entry in entries.decode().split("\0"):
                if not entry:
                    continue
                metadata, path = entry.split("\t", 1)
                mode, object_kind, _identity = metadata.split(" ")
                if mode != "100644" or object_kind != "blob":
                    return False
                seen.add(path)
    except (subprocess.CalledProcessError, OSError, UnicodeError, ValueError):
        return False
    if seen != set(changes):
        return False
    return True


def plan(root):
    components = registry(root)
    event = read_json(Path(os.environ["GITHUB_EVENT_PATH"])) if os.environ.get("GITHUB_EVENT_PATH") else {}
    kind = os.environ.get("GITHUB_EVENT_NAME")
    cold = kind == "schedule" or event.get("inputs", {}).get("cold") in {True, "true"}
    base = event.get("pull_request", {}).get("base", {}).get("sha") or event.get("before")
    changes = None
    if isinstance(base, str) and re.fullmatch(r"[0-9a-f]{40}", base) and set(base) != {"0"}:
        try:
            changes = git(root, "diff", "--no-renames", "--name-only", "-z", base, "HEAD").decode().strip("\0").split("\0")
        except (subprocess.CalledProcessError, UnicodeError):
            # An unavailable comparison selects every ready component; it never skips work.
            pass
    matrix, unavailable = selected(components, changes, cold)
    mode = "documentation" if documentation_change(root, base, kind, changes, cold, components) else "full"
    if mode == "documentation":
        baseline = components.get("fastjsonschema", {})
        require(baseline.get("status") == "ready" and baseline.get("always") is True,
                "Documentation validation requires the ready validator baseline")
        matrix["include"] = [entry for entry in matrix["include"] if entry["component"] == "fastjsonschema"]
        require(bool(matrix["include"]), "Documentation validation cannot omit the validator baseline")
    result = {"matrix": matrix, "unavailable": unavailable, "cold": cold, "mode": mode}
    print(json.dumps(result, indent=2))
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write("matrix=" + json.dumps(matrix, separators=(",", ":")) + "\n")
            output.write("mode=" + mode + "\n")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
            summary.write("## Dependency build selection\n\n")
            summary.write(f"Validation mode: {mode}. Native packages are {'selected' if mode == 'full' else 'not selected'}.\n\n")
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
    parser.add_argument("--reuse-prepared-sources", action="store_true",
                        help="Revalidate and reuse this job's existing explicit Git source exports")
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
                build(root, args.component, args.platform, args.cold,
                      reuse_prepared_sources=args.reuse_prepared_sources)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, tarfile.TarError, zipfile.BadZipFile):
        print("Dependency validation/build failed; consult the failing stage output", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
