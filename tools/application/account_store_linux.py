"""Build the isolated Secret Service adapter against the already owned D-Bus runtime."""

from pathlib import Path
import platform
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def build(output, renderer, cc):
    if (platform.system(), platform.machine()) != ("Linux", "x86_64"):
        raise ValueError("The account store helper requires its native Linux x64 host")
    library = renderer / "runtime/libdbus-1.so.3"
    if not library.is_file() or library.is_symlink():
        raise ValueError("Missing owned D-Bus runtime")
    original = ROOT / "3rdparty/desktop-dbus"
    # Match the original Meson type selection for this explicitly supported ABI.
    # The original header, copyright and license remain intact; only its documented configure variables change.
    macros = subprocess.check_output([cc, "-dM", "-E", "-"], input=b"", timeout=30).decode()
    for name, value in {"LONG": 8, "INT": 4, "SHORT": 2, "POINTER": 8}.items():
        if not re.search(rf"^#define __SIZEOF_{name}__ {value}$", macros, re.M):
            raise ValueError("D-Bus header configuration requires the checked Linux LP64 ABI")
    version = re.search(r"version: '(\d+)\.(\d+)\.(\d+)'", (original / "meson.build").read_text())
    if version is None:
        raise ValueError("Missing original D-Bus version")
    values = {"DBUS_INT64_TYPE": "long", "DBUS_INT64_MODIFIER": "l", "DBUS_INT64_CONSTANT": "(val##L)",
              "DBUS_UINT64_CONSTANT": "(val##UL)", "DBUS_INT32_TYPE": "int", "DBUS_INT16_TYPE": "short",
              "DBUS_SIZEOF_VOID_P": "8", "DBUS_MAJOR_VERSION": version[1], "DBUS_MINOR_VERSION": version[2],
              "DBUS_MICRO_VERSION": version[3], "DBUS_VERSION": ".".join(version.groups())}
    header = (original / "dbus/dbus-arch-deps.h.in").read_text()
    for key, value in values.items():
        header = header.replace("@" + key + "@", value)
    if re.search(r"@[A-Z_0-9]+@", header):
        raise ValueError("Unconfigured original D-Bus header variable")
    with tempfile.TemporaryDirectory(prefix="sw-account-store-build-") as temporary:
        include = Path(temporary)
        (include / "dbus").mkdir()
        (include / "dbus/dbus-arch-deps.h").write_text(header)
        subprocess.run([cc, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-I" + str(include), "-I" + str(original),
                        str(ROOT / "packages/linux-room/native/account_store.c"), str(library),
                        "-ldl", "-Wl,-z,defs,-z,relro,-z,now", "-Wl,-rpath,$ORIGIN/Renderer/runtime", "-o", str(output)],
                       check=True, timeout=45)
