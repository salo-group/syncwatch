"""Configure selected modules without changing interpreter algorithms."""

from pathlib import Path
import re
import xml.etree.ElementTree as ET

from source_profile import digest, encoded

NS = "http://schemas.microsoft.com/developer/msbuild/2003"
UNIX_MAKEFILE = Path(__file__).resolve().with_name("unix-runtime.mk")
WINDOWS_TARGETS = Path(__file__).resolve().with_name("windows-runtime.targets")


def unix_make(*arguments):
    return ["make", "-f", "Makefile", "-f", UNIX_MAKEFILE.name, *arguments]


def unix_setup(source, build, zlib, library, profile, module_flags=None):
    names = re.findall(r"^@MODULE_[A-Z0-9_]+_TRUE@([a-zA-Z0-9_]+) ",
                       (source / "Modules/Setup.stdlib.in").read_text(), re.M)
    selected = profile["extensions"]
    lines = ["# Configured by SyncWatch embedded-core-v1; original algorithms unchanged.", "*static*"]
    for name, files in selected.items():
        lines.append(name + " " + " ".join(Path(p).relative_to("Modules").as_posix() for p in files)
                     + (" " + " ".join(module_flags[name]) if module_flags and name in module_flags else ""))
    # No '=' in a makesetup module line: it denotes a Makefile assignment.
    lines.append("zlib zlibmodule.c -DZ_PREFIX -I" + zlib.as_posix() + " " + library.as_posix())
    lines += ["*disabled*", *(name for name in names if name not in selected and name != "zlib")]
    path = build / "Modules/Setup.local"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    (build / UNIX_MAKEFILE.name).write_bytes(UNIX_MAKEFILE.read_bytes())


def builtin_modules(table):
    return tuple(sorted(re.findall(r'^\s*\{"([a-zA-Z_][a-zA-Z0-9_]*)",', table.decode("utf-8"), re.M)))


def windows_modules(cpython, profile):
    bootstrap = cpython["Modules/Setup.bootstrap.in"].decode("utf-8")
    names = set(re.findall(r"^(?:@MODULE_[A-Z0-9_]+_TRUE@)?([a-zA-Z_][a-zA-Z0-9_]*) ", bootstrap, re.M))
    names.update(profile["extensions"])
    names.difference_update(["posix", "pwd"])
    names.update(["nt", "gc", "msvcrt", "winreg", "zlib", "_imp", "_string", "_ast", "_tokenize"])
    # Socket/select remain separate native extension DLLs in the upstream build.
    names.difference_update(profile.get("windowsShared", ["_socket", "select"]))
    standard = set(builtin_modules(cpython["Modules/config.c.in"]))
    return sorted(names - standard), tuple(sorted(names | standard))


def require_builtin_table(table, expected):
    if not table.is_file() or builtin_modules(table.read_bytes()) != expected:
        raise ValueError("CPython built-in module table differs from the pinned Windows profile")


def builtin_probe(expected):
    return f"\nassert sys.builtin_module_names == {expected!r}\n" if expected else ""


def windows_setup(cpython, build, profile):
    bootstrap = cpython["Modules/Setup.bootstrap.in"].decode("utf-8")
    files = set(re.findall(r"[a-zA-Z0-9_/]+\.c", "\n".join(line for line in bootstrap.splitlines() if not line.startswith("#"))))
    files.update(Path(path).relative_to("Modules").as_posix()
                 for sources in profile["extensions"].values() for path in sources)
    files.update(["gcmodule.c", "getbuildinfo.c", "getpath.c", "main.c", "zlibmodule.c", "_io/winconsoleio.c"])
    project = ET.fromstring(cpython["PCbuild/pythoncore.vcxproj"])
    selection = ET.Element("Project", xmlns=NS)
    group = ET.SubElement(selection, "ItemGroup")
    for item in project.findall(".//m:ClCompile[@Include]", {"m": NS}):
        include = item.get("Include")
        path = include.replace("\\", "/")
        if path.startswith("../Modules/") and path.removeprefix("../Modules/") not in files:
            ET.SubElement(group, "ClCompile", Remove=include)
    build.mkdir(parents=True, exist_ok=True)
    ET.indent(selection, space="    ")
    ET.ElementTree(selection).write(build / "module-selection.props", encoding="utf-8", xml_declaration=True)
    return windows_modules(cpython, profile)


def build_recipe(target):
    paths = ([WINDOWS_TARGETS, WINDOWS_TARGETS.with_name("build_metadata.targets")]
             if target == "windows-x64" else [UNIX_MAKEFILE])
    return {path.name: path.read_bytes() for path in paths}


def build_configuration(build, target):
    recipe = build_recipe(target)
    if target == "windows-x64":
        return recipe | {name: (build / name).read_bytes() for name in ["config.c", "module-selection.props"]}
    if (build / UNIX_MAKEFILE.name).read_bytes() != recipe[UNIX_MAKEFILE.name]:
        raise ValueError("CPython loaded a different Unix Make configuration")
    # Configure output contains build-machine paths. Retain its identities.
    hashes = {name: digest((build / name).read_bytes()) for name in ["Makefile", "Modules/Setup.local"]}
    return recipe | {"generated-build-digests.json": encoded(hashes)}


def assert_unix_membership(build, profile):
    text = (build / "Makefile").read_text()
    shared = re.findall(r"^MODSHARED_NAMES= *(.*)$", text, re.M)
    if shared != [""]:
        raise ValueError("Unexpected auto-detected shared modules in the embedded runtime")
    built = re.findall(r"^MODBUILT_NAMES= *(.*)$", text, re.M)
    if len(built) != 1 or not set(profile["extensions"]) | {"zlib"} <= set(built[0].split()):
        raise ValueError("Configured embedded runtime is missing required modules")
