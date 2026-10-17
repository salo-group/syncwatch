"""Validate first-party repository content without installed packages."""

import argparse
import ast
import configparser
import json
import math
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET


class ValidationError(ValueError):
    """A repository artifact does not meet its declared policy."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def load_json(path: Path) -> object:
    def unique_keys(pairs: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in pairs:
            require(key not in result, f"{path}: duplicate JSON key {key}")
            result[key] = value
        return result

    def invalid_number(value: str) -> None:
        raise ValidationError(f"{path}: non-finite JSON number {value}")

    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=unique_keys,
            parse_constant=invalid_number,
        )
    except (OSError, UnicodeError, ValueError) as error:
        raise ValidationError(f"{path}: {error}") from error


def owned_files(root: Path) -> list[Path]:
    output = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
    )
    paths = {name.decode("utf-8") for name in output.split(b"\0") if name}
    return [
        root / name
        for name in sorted(paths)
        if not name.startswith("3rdparty/") and (root / name).is_file()
    ]


def check_text(path: Path) -> None:
    data = path.read_bytes()
    require(b"\r" not in data, f"{path}: use LF line endings")
    require(not data or data.endswith(b"\n"), f"{path}: missing final newline")
    text = data.decode("utf-8")
    for number, line in enumerate(text.splitlines(), 1):
        require(line == line.rstrip(" \t"), f"{path}:{number}: trailing whitespace")
        if path.suffix in {".py", ".json", ".yml", ".yaml", ".md", ".toml"}:
            require(not line.startswith("\t"), f"{path}:{number}: use space indentation")
    if path.suffix == ".py":
        ast.parse(text, filename=str(path), feature_version=(3, 12))


def check_document(path: Path, root: Path) -> None:
    text = path.read_text(encoding="utf-8")
    require(not re.search(r"[\u0400-\u04ff]", text), f"{path}: write documentation in English")
    require(
        not re.search(r"/(?:Users|home)/|[A-Za-z]:\\Users\\", text),
        f"{path}: documentation must be self-contained product documentation",
    )
    visible = re.sub(r"^(```|~~~).*?^\1[^\n]*$", "", text, flags=re.M | re.S)
    targets = re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", visible)
    targets += re.findall(r"^\[[^\]]+\]:\s*(\S+)", visible, flags=re.M)
    for target in targets:
        target = target.strip().split(' "', 1)[0].strip("<>")
        link = urlsplit(target)
        if link.scheme:
            require(link.scheme in {"https", "http", "mailto"}, f"{path}: unsupported link {target}")
            continue
        if not link.path:
            continue
        require(not link.netloc, f"{path}: use explicit HTTPS links")
        destination = (path.parent / unquote(link.path)).resolve()
        require(destination.is_relative_to(root.resolve()), f"{path}: link leaves the repository")
        require(destination.exists(), f"{path}: broken local link {target}")


def check_design(root: Path) -> None:
    design = root / "packages/design"
    package = load_json(design / "package.json")
    require(isinstance(package, dict), "design package must be an object")
    require(package.get("license") == "Apache-2.0", "design license must be Apache-2.0")
    require(isinstance(package.get("files"), list), "design package files must be a list")
    for member in package["files"]:
        require(isinstance(member, str), "design package file must be a string")
        destination = (design / member).resolve()
        require(destination.is_relative_to(design.resolve()), "design package path escapes the package")
        require(destination.exists(), f"missing design package asset {member}")

    tokens = {}

    def collect(value: object, name: str = "") -> None:
        require(isinstance(value, dict), f"invalid token group {name}")
        if "value" in value:
            require(name not in tokens, f"duplicate token {name}")
            kind, scalar = value.get("type"), value["value"]
            if kind == "color":
                require(
                    isinstance(scalar, str) and re.fullmatch(r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?", scalar),
                    f"invalid color token {name}",
                )
            elif kind == "number":
                require(
                    type(scalar) in {int, float} and math.isfinite(scalar) and scalar >= 0,
                    f"invalid numeric token {name}",
                )
            else:
                raise ValidationError(f"unsupported design token type {kind}")
            tokens[name] = value
            return
        for key, child in value.items():
            collect(child, f"{name}.{key}" if name else key)

    for path in sorted((design / "tokens").rglob("*.json")):
        collect(load_json(path))
    require(bool(tokens), "design tokens must not be empty")
    contract = load_json(design / "components.json")
    require(isinstance(contract, dict) and contract.get("contractVersion") == 1, "unsupported component contract")
    components = contract.get("components")
    require(isinstance(components, dict) and bool(components), "components must not be empty")
    for name, component in components.items():
        require(isinstance(component, dict), f"invalid component {name}")
        for field in ("tokens", "states", "actions"):
            values = component.get(field, [])
            require(isinstance(values, list), f"{name}.{field} must be a list")
            require(all(isinstance(item, str) for item in values), f"{name}.{field} must contain strings")
            require(len(values) == len(set(values)), f"duplicate {name}.{field}")
        for reference in component.get("tokens", []):
            require(reference in tokens, f"{name}: unknown token {reference}")


def check_submodules(root: Path) -> None:
    config = configparser.ConfigParser(interpolation=None, strict=True)
    config.read(root / ".gitmodules", encoding="utf-8")
    modules = {}
    for section in config.sections():
        require(re.fullmatch(r'submodule "[^"]+"', section) is not None, "invalid submodule section")
        entry = config[section]
        require(set(entry) == {"path", "url"}, f"{section}: require path and URL without floating branches")
        path, url = entry["path"], urlsplit(entry["url"])
        require(re.fullmatch(r"3rdparty/[a-zA-Z0-9_.-]+", path) is not None, f"invalid submodule path {path}")
        require(path not in modules, f"duplicate submodule path {path}")
        require(
            url.scheme == "https" and bool(url.hostname) and not url.username
            and not url.password and not url.query and not url.fragment,
            f"{path}: require a credential-free HTTPS source URL",
        )
        modules[path] = entry["url"]
    entries = subprocess.check_output(["git", "ls-files", "--stage", "-z"], cwd=root)
    links = {}
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        metadata, name = entry.decode("utf-8").split("\t", 1)
        mode, revision, stage = metadata.split()
        require(stage == "0", f"unmerged index entry {name}")
        if mode == "160000":
            require(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", revision) is not None, f"invalid source pin {name}")
            require(set(revision) != {"0"}, f"empty source pin {name}")
            links[name] = revision
        elif name.startswith("3rdparty/"):
            raise ValidationError(f"vendor source must be a Git submodule: {name}")
    require(bool(modules), "no source submodules are registered")
    require(set(modules) == set(links), "submodule registrations and pinned Git links differ")


def check_workflows(root: Path) -> None:
    paths = sorted((root / ".github/workflows").glob("*.y*ml"))
    require(len(paths) == 1, "use one repository workflow until native lanes are introduced")
    workflow = load_json(paths[0])
    require(isinstance(workflow, dict), "workflow must be a JSON-compatible YAML object")
    require(workflow.get("permissions") == {"contents": "read"}, "CI must use read-only repository permissions")
    events = workflow.get("on", {})
    require(set(events) == {"pull_request", "push", "merge_group", "workflow_dispatch"}, "missing or unsafe workflow trigger")
    require(events["pull_request"] == {}, "pull request checks must not use path filters")
    require(events["push"] == {"branches": ["main"]}, "main pushes must run without path filters")
    require(workflow.get("concurrency", {}).get("cancel-in-progress") is True, "superseded checks must cancel")
    jobs = workflow.get("jobs", {})
    require(set(jobs) == {"repository", "tests", "contracts", "required"}, "unexpected repository job set")
    for name, job in jobs.items():
        require(job.get("runs-on") == "ubuntu-24.04", f"{name}: use the declared runner image")
        require(type(job.get("timeout-minutes")) is int and 0 < job["timeout-minutes"] <= 15, f"{name}: require a bounded timeout")
        require(not job.get("continue-on-error"), f"{name}: failures must propagate")
        require("permissions" not in job and "secrets" not in job and "environment" not in job, f"{name}: no privileged job overrides")
        for step in job.get("steps", []):
            require("uses" not in step, "use runner tools and repository-owned scripts")
            require(not step.get("continue-on-error"), "step failures must propagate")
    commands = {step.get("run") for step in jobs["contracts"].get("steps", [])}
    require(
        {
            "git submodule update --init --checkout --depth=1 -- 3rdparty/fastjsonschema",
            "python3 tools/contracts/check_contracts.py",
            "python3 -m unittest discover -s tools/contracts/tests -v",
        }.issubset(commands),
        "contract job must initialize pinned sources and execute its checks and tests",
    )
    required = jobs["required"]
    require(required.get("name") == "ci/required", "required check name must remain stable")
    require(required.get("if") == "${{ always() }}", "aggregate must evaluate unsuccessful dependencies")
    require(required.get("needs") == ["repository", "tests", "contracts"], "aggregate must cover every check")
    final = required["steps"][-1]
    require(final.get("run") == "python3 tools/ci/required_status.py repository tests contracts", "aggregate must validate the selected results")
    require(final.get("env") == {"CI_NEEDS_JSON": "${{ toJSON(needs) }}"}, "aggregate must inspect actual job results")


def check_native_manifests(root: Path) -> None:
    toolchains = load_json(root / "tools/native/toolchains.json")
    sdk = load_json(root / "global.json")["sdk"]
    require(sdk == {
        "version": toolchains["windows"]["dotnetSdk"], "rollForward": "disable", "allowPrerelease": False,
    }, "native SDK selection must match the exact toolchain pin")
    sources = ET.parse(root / "NuGet.Config").getroot()
    for name in ("packageSources", "auditSources"):
        section = sources.find(name)
        require(section is not None and len(section) == 1 and section[0].tag == "clear", "native restore must use no registry sources")
    solution = ET.parse(root / "SyncWatch.slnx").getroot()
    projects = solution.findall(".//Project")
    require(len(projects) == 3, "native solution must include the shell, domain, and executable tests")
    for project in projects:
        path = (root / project.attrib["Path"]).resolve()
        require(path.is_relative_to(root.resolve()) and path.is_file(), "native project must be a repository file")
        content = ET.parse(path).getroot()
        require(not content.findall(".//PackageReference"), "native libraries must use pinned local sources")
        expected = toolchains["windows"]["targetFramework"] if path.stem == "SyncWatch" else "net10.0"
        require(content.findtext(".//TargetFramework") == expected, "native target framework does not match its manifest")
        for reference in content.findall(".//ProjectReference"):
            target = (path.parent / reference.attrib["Include"]).resolve()
            require(target.is_relative_to(root.resolve()) and target.is_file(), "native project reference must stay local")


def check_repository(root: Path) -> None:
    paths = owned_files(root)
    for path in paths:
        check_text(path)
        if path.suffix == ".json":
            load_json(path)
        if path.suffix == ".md":
            check_document(path, root)
    check_design(root)
    check_submodules(root)
    check_workflows(root)
    check_native_manifests(root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    try:
        check_repository(args.root.resolve())
    except (OSError, ValueError, SyntaxError, ET.ParseError, configparser.Error, subprocess.CalledProcessError) as error:
        print(f"Repository validation failed: {error}", file=sys.stderr)
        return 1
    print("Repository documentation, design, source pins, text, and workflow policy are valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
