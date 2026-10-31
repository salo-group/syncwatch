"""Retain original sources, complete grants and the recipes needed to rebuild."""

import subprocess

from source_profile import ROOT
from source_files import archive_bytes, digest, encoded
from configuration import vlc_files, pulse_version
from sdk_material import notices
from font_configuration import contents as font_contents


def recipes():
    files = {"LICENSE": (ROOT / "LICENSE").read_bytes()}
    names = subprocess.check_output(["git", "ls-files", "-z", "--", "tools/vlc", "tools/compression", "tools/native/native_context.py",
         "tools/native/native_libraries.py", "tools/native/native_fonts.py", "tools/native/native_x11.py"], cwd=ROOT)
    for name in names.decode().split("\0"):
        if name and "/tests/" not in name:
            path = ROOT / name
            if not path.is_file() or path.is_symlink():
                raise ValueError("Native media recipes require regular tracked source files")
            files[name] = path.read_bytes()
    files["tools/dependencies/source_files.py"] = (ROOT / "tools/dependencies/source_files.py").read_bytes()
    files["tools/dependencies/components.json"] = (ROOT / "tools/dependencies/components.json").read_bytes()
    return files


def expected(snapshots, profile, context):
    files = {"sources/recipes.zip": archive_bytes(recipes()),
             "sources/profile.json": encoded(profile)}
    files.update(notices(context))
    if context.target == "linux-x64":
        files.update(font_contents(snapshots["fontconfig"], profile))
        # Connect to the existing user audio service; never start a daemon as a
        # side effect of opening a movie or probing an unavailable output.
        files["share/pulse/client.conf"] = b"# SyncWatch native audio client\nautospawn = no\n"
        files["sources/configured/pulseaudio/.tarball-version"] = pulse_version(snapshots["pulseaudio"], profile)
    for name, data in vlc_files(snapshots["vlc"], profile).items():
        files["sources/configured/vlc/" + name] = data
    inventory = {}
    for name, specification in sorted(profile["sources"].items()):
        original = snapshots[name]
        archive = archive_bytes(original)
        files["sources/" + name + ".zip"] = archive
        inventory[name] = {"revision": specification["revision"], "path": specification["path"],
                           "license": specification["license"], "sourceSha256": digest(archive),
                           "files": len(original), "executables": specification["executables"],
                           "notices": specification["notices"]}
        # Keep the original file hierarchy and complete bytes, including source
        # headers with embedded grants. Nothing is replaced by an SPDX label.
        for notice in specification["notices"]:
            files["notices/" + name + "/" + notice] = original[notice]
    files["sources/inventory.json"] = encoded(inventory)
    header = snapshots["freetype"]["include/freetype/freetype.h"].decode()
    if "2026" not in header.split("*/", 1)[0]:
        raise ValueError("Review the FreeType version credit before packaging")
    files["THIRD_PARTY.txt"] = (
        "SyncWatch native media SDK\n\n"
        "This package contains dynamically linked libVLC and codec/font libraries.\n"
        "Part of this software is copyright (C) 2026 The FreeType Project\n"
        "(www.freetype.org). All rights reserved.\n\n"
        "Complete original grants and credits are in notices/ and the original\n"
        "source files in sources/*.zip. sources/inventory.json records revisions,\n"
        "licenses, exact archive digests and original executable-file inventories.\n"
        "Source ZIPs preserve original bytes; use the recorded executable inventory\n"
        "when materializing them. Sources include build-only and excluded modules;\n"
        "their presence does not imply that those modules are linked.\n\n"
        "The selected decoder profile disables FFmpeg GPL, version3 and nonfree\n"
        "components. FreeType uses the FreeType License choice. Upstream original\n"
        "license alternatives remain intact in the corresponding sources.\n\n"
        "Rebuild with the Apache-2.0 first-party recipes in sources/recipes.zip,\n"
        "the pinned snapshots, profile and a compatible native developer SDK.\n"
        "The SDK tools are external build inputs, not application dependencies.\n"
        "Configured VLC metadata, player filter inheritance, embedded subtitle codec\n"
        "mapping, audio/video-only avformat clock, complete decoder picture reset\n"
        "and GNU iconv restoration\n"
        "are declared by those recipes. Configured VLC files are also retained in\n"
        "sources/configured/vlc/ with their original grants and a change notice;\n"
        "recipes; original source bytes remain unchanged in the source archives.\n"
        "Keep the dynamic libraries replaceable and retain corresponding sources\n"
        "and grants when distributing this SDK or an application containing it.\n\n"
        "Native client surfaces and physical output are not qualified by this\n"
        "memory-callback decoder package. Hardware diagnostics are separately\n"
        "reported; H.264 VideoToolbox backward seeking is not qualified.\n"
    ).encode()
    return files


def write(package, contents):
    for name, data in contents.items():
        path = package / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
