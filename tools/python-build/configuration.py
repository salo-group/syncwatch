"""Configure selected modules without changing interpreter algorithms."""

from pathlib import Path
import re
import xml.etree.ElementTree as ET

NS = "http://schemas.microsoft.com/developer/msbuild/2003"
UNIX_MAKEFILE = Path(__file__).resolve().with_name("unix-runtime.mk")


def unix_make(*arguments):
    return ["make", "-f", "Makefile", "-f", str(UNIX_MAKEFILE), *arguments]


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


def module_sources(source, profile):
    bootstrap = (source / "Modules/Setup.bootstrap.in").read_text()
    names = set(re.findall(r"^(?:@MODULE_[A-Z0-9_]+_TRUE@)?([a-zA-Z_][a-zA-Z0-9_]*) ", bootstrap, re.M))
    files = set(re.findall(r"[a-zA-Z0-9_/]+\.c", "\n".join(l for l in bootstrap.splitlines() if not l.startswith("#"))))
    files.update(Path(p).relative_to("Modules").as_posix() for values in profile["extensions"].values() for p in values)
    names.update(profile["extensions"])
    return names, files


def windows_setup(source, profile):
    names, files = module_sources(source, profile)
    names.discard("posix"); names.discard("pwd")
    names.update(["nt", "gc", "msvcrt", "winreg", "zlib", "_imp", "_string", "_ast", "_tokenize"])
    # Socket/select remain separate native extension DLLs in the upstream build.
    names.difference_update(profile.get("windowsShared", ["_socket", "select"]))
    files.update(["gcmodule.c", "getbuildinfo.c", "getpath.c", "main.c", "zlibmodule.c", "_io/winconsoleio.c"])
    config = (source / "PC/config.c").read_text(encoding="utf-8")
    mapping = dict(re.findall(r'\{"([a-zA-Z0-9_]+)", (PyInit_[a-zA-Z0-9_]+)\}', config))
    for name, function in mapping.items():
        if name not in names:
            config = re.sub(r"^extern PyObject\* " + function + r"\(void\);\n", "", config, flags=re.M)
            config = re.sub(r'^    \{"' + name + r'", ' + function + r'\},[^\n]*\n', "", config, flags=re.M)
    marker = "/* Configured by SyncWatch embedded-core-v1; original algorithms unchanged. */\n"
    (source / "PC/config.c").write_text(marker + config, encoding="utf-8", newline="\n")
    ET.register_namespace("", NS)
    project = source / "PCbuild/pythoncore.vcxproj"
    tree = ET.parse(project)
    for group in tree.getroot():
        for item in list(group):
            value = item.get("Include", "").replace("\\", "/")
            if item.tag.rsplit("}", 1)[-1] == "ClCompile" and value.startswith("../Modules/") and value[11:] not in files:
                group.remove(item)
    flags = tree.find("m:ItemDefinitionGroup/m:ClCompile/m:PreprocessorDefinitions", {"m": NS})
    if flags is None:
        raise ValueError("Original Windows core compile configuration is missing")
    flags.text = "Z_PREFIX;" + flags.text
    tree.write(project, encoding="utf-8", xml_declaration=True)
    # Deterministic SDK compilation and stable embedded PDB names. These flags
    # configure build metadata; original interpreter/extension algorithms stay intact.
    for name in ["pythoncore", "_socket", "select"]:
        project = source / "PCbuild" / (name + ".vcxproj")
        tree = ET.parse(project)
        group = ET.SubElement(tree.getroot(), "{" + NS + "}ItemDefinitionGroup")
        compile_options = ET.SubElement(ET.SubElement(group, "{" + NS + "}ClCompile"), "{" + NS + "}AdditionalOptions")
        compile_options.text = ('/experimental:deterministic "/pathmap:' + str(source) + '=cpython-source" '
                                '"/pathmap:' + str(source.parent) + '=syncwatch-build" %(AdditionalOptions)')
        link_options = ET.SubElement(ET.SubElement(group, "{" + NS + "}Link"), "{" + NS + "}AdditionalOptions")
        link_options.text = "/Brepro /PDBALTPATH:$(TargetName).pdb %(AdditionalOptions)"
        tree.write(project, encoding="utf-8", xml_declaration=True)
    project = source / "PCbuild/pcbuild.proj"
    tree = ET.parse(project)
    for group in tree.getroot():
        for item in list(group):
            kind, value = item.tag.rsplit("}", 1)[-1], item.get("Include")
            if kind == "Projects" and value is not None and value not in ["pythoncore.vcxproj", "python3dll.vcxproj"]:
                group.remove(item)
            elif kind == "Projects2" and value is not None and value != "python.vcxproj;pythonw.vcxproj":
                group.remove(item)
    # Exact matching above deliberately retains FreezeProjects and its metadata.
    if tree.find(".//m:FreezeProjects[@Include='_freeze_module.vcxproj']", {"m": NS}) is None:
        raise ValueError("Windows source bootstrap must retain its original freeze project")
    tree.write(project, encoding="utf-8", xml_declaration=True)


def assert_unix_membership(build, profile):
    text = (build / "Makefile").read_text()
    shared = re.findall(r"^MODSHARED_NAMES= *(.*)$", text, re.M)
    if shared != [""]:
        raise ValueError("Unexpected auto-detected shared modules in the embedded runtime")
    built = re.findall(r"^MODBUILT_NAMES= *(.*)$", text, re.M)
    if len(built) != 1 or not set(profile["extensions"]) | {"zlib"} <= set(built[0].split()):
        raise ValueError("Configured embedded runtime is missing required modules")
