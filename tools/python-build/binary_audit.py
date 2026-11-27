"""Reject undeclared shared-library dependencies in the selected native runtime."""

import re
import subprocess
from pathlib import Path


def require_bound_macos_symbols(path):
    symbols = subprocess.check_output(["nm", "-u", "-m", str(path)],
                                      text=True, stderr=subprocess.STDOUT, timeout=30)
    if "(dynamically looked up)" in symbols:
        raise ValueError("CPython links symbols without a declared macOS library")


def audit(paths, target, sdk, options):
    records = {}
    for path in [paths["runtime"], *paths["extensions"]]:
        if target == "macos-arm64":
            require_bound_macos_symbols(path)
            command = ["otool", "-L", str(path)]
            allowed = {"@rpath/libpython3.12.dylib", "/usr/lib/libSystem.B.dylib",
                "/System/Library/Frameworks/CoreFoundation.framework/Versions/A/CoreFoundation"}
        elif target == "linux-x64":
            command = ["readelf", "-d", str(path)]
            allowed = {"libc.so.6", "libm.so.6", "libdl.so.2", "libpthread.so.0", "libutil.so.1", "ld-linux-x86-64.so.2"}
        else:
            instance = next(v.split("=", 1)[1] for v in options if v.startswith("-DCMAKE_GENERATOR_INSTANCE="))
            dumpbin = Path(instance) / "VC/Tools/MSVC" / sdk["compiler"].split()[1] / "bin/Hostx64/x64/dumpbin.exe"
            command = [str(dumpbin), "/nologo", "/dependents", str(path)]
            allowed = {"KERNEL32.DLL", "ADVAPI32.DLL", "WS2_32.DLL", "SHELL32.DLL", "USER32.DLL",
                "VERSION.DLL", "OLE32.DLL", "OLEAUT32.DLL", "BCRYPT.DLL", "UCRTBASE.DLL",
                "PATHCCH.DLL", "IPHLPAPI.DLL", "RPCRT4.DLL",
                "API-MS-WIN-CORE-PATH-L1-1-0.DLL",
                "VCRUNTIME140.DLL", "VCRUNTIME140_1.DLL", "PYTHON312.DLL"}
        output = subprocess.check_output(command, text=True, stderr=subprocess.STDOUT, timeout=30)
        if target == "macos-arm64":
            dependencies = re.findall(r"^\s+(\S+) \(compatibility version", output, re.M)
        elif target == "linux-x64":
            dependencies = re.findall(r"\(NEEDED\).*\[([^\]]+)\]", output)
        else:
            dependencies = [n.upper() for n in re.findall(r"^\s+([A-Za-z0-9_.-]+\.dll)\s*$", output, re.M | re.I)]
        if not dependencies or any(n not in allowed and not (target == "windows-x64" and re.fullmatch(r"API-MS-WIN-CRT-[A-Z0-9-]+\.DLL", n)) for n in dependencies):
            raise ValueError("Runtime links an undeclared native dependency: " + repr(dependencies))
        records[path.name] = sorted(dependencies)
    return records
