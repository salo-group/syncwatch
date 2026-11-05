"""Build the pinned codec, text and font libraries with implicit downloads disabled."""

import json
import shutil

from build_context import shell_path
from source_profile import restore_iconv
from native_libraries import cmake, meson, zlib
from native_x11 import build as build_x11
from configuration import pulse_version, ffmpeg_files
from source_files import digest


def build(context, sources, profile):
    if context.target == "linux-x64":
        build_x11(context, sources)
        cmake(context, "libsndfile", sources["libsndfile"], ["-DBUILD_SHARED_LIBS=ON", "-DBUILD_TESTING=OFF",
            "-DBUILD_PROGRAMS=OFF", "-DBUILD_EXAMPLES=OFF", "-DBUILD_REGTEST=OFF", "-DENABLE_CPACK=OFF",
            "-DENABLE_EXTERNAL_LIBS=OFF", "-DENABLE_MPEG=OFF"])
        original = {name: (sources["pulseaudio"] / name).read_bytes() for name in ("NEWS", "meson.build", "git-version-gen")}
        (sources["pulseaudio"] / ".tarball-version").write_bytes(pulse_version(original, profile))
        meson(context, sources, "pulseaudio", ["-Ddaemon=false", "-Dclient=true", "-Dtests=false", "-Dman=false",
            "-Ddoxygen=false", "-Ddbus=disabled", "-Dglib=disabled", "-Dgtk=disabled", "-Dx11=disabled",
            "-Dasyncns=disabled", "-Dsystemd=disabled", "-Dbashcompletiondir=no", "-Dzshcompletiondir=no"])
    if context.target == "windows-x64":
        # GCC's POSIX thread ABI is also used by the codec/runtime libraries.
        # Build its provider from the separately pinned source, never copy the
        # developer SDK's prebuilt winpthread DLL into the application payload.
        build = context.work / "winpthreads-build"
        build.mkdir()
        context.run("winpthreads-configure", [shell_path(sources["winpthreads"] / "mingw-w64-libraries/winpthreads/configure"),
            "--prefix=" + shell_path(context.prefix), "--host=x86_64-w64-mingw32", "--build=x86_64-pc-msys",
            "--enable-shared", "--disable-static"], cwd=build, shell=True)
        context.run("winpthreads-build", ["make", "-j4"], cwd=build, shell=True)
        context.run("winpthreads-install", ["make", "install"], cwd=build, shell=True)
    zlib(context, sources)
    cmake(context, "libsamplerate", sources["libsamplerate"], ["-DBUILD_SHARED_LIBS=ON",
        "-DBUILD_TESTING=OFF", "-DLIBSAMPLERATE_EXAMPLES=OFF", "-DLIBSAMPLERATE_INSTALL=ON"])
    if context.target == "windows-x64":
        original = restore_iconv(sources["libiconv"])
        build = context.work / "iconv-build"
        build.mkdir()
        context.run("iconv-configure", [shell_path(original / "configure"),
            "--prefix=" + shell_path(context.prefix), "--enable-shared", "--disable-static",
            "--disable-nls", "--disable-rpath", "--host=x86_64-w64-mingw32",
            "--build=x86_64-pc-msys"], cwd=build, shell=True)
        # Only the LGPL libraries and headers are built/installed, not the CLI.
        for part in ["libcharset", "lib"]:
            context.run("iconv-" + part, ["make", "-C", part, "-j4", "all", "install"], cwd=build, shell=True)
        # The top-level install recipe copies this configured public header.
        # include/ has no Makefile; top-level all also builds the GPL CLI.
        shutil.copy2(build / "include/iconv.h.inst", context.prefix / "include/iconv.h")
    cmake(context, "freetype", sources["freetype"], ["-DBUILD_SHARED_LIBS=ON", "-DFT_DISABLE_ZLIB=OFF",
        "-DZLIB_LIBRARY=" + str(context.prefix / "lib/libz.a"), "-DZLIB_INCLUDE_DIR=" + str(context.prefix / "include"),
        "-DFT_DISABLE_BZIP2=ON", "-DFT_DISABLE_PNG=ON", "-DFT_DISABLE_HARFBUZZ=ON", "-DFT_DISABLE_BROTLI=ON"])
    meson(context, sources, "harfbuzz", ["-Dfreetype=enabled", "-Dtests=disabled", "-Dutilities=disabled",
        "-Ddocs=disabled", "-Dsubset=disabled", "-Draster=disabled", "-Dvector=disabled", "-Dgpu=disabled"])
    meson(context, sources, "fribidi", ["-Ddocs=false", "-Dbin=false", "-Dtests=false"])
    if context.target == "linux-x64":
        cmake(context, "expat", sources["expat"] / "expat", ["-DEXPAT_BUILD_TOOLS=OFF", "-DEXPAT_BUILD_EXAMPLES=OFF",
            "-DEXPAT_BUILD_TESTS=OFF", "-DEXPAT_BUILD_DOCS=OFF", "-DEXPAT_SHARED_LIBS=ON"])
        meson(context, sources, "fontconfig", ["-Dxml-backend=expat", "-Dtests=disabled",
            "-Dtests-external-fonts=disabled", "-Ddoc=disabled", "-Dtools=disabled", "-Dnls=disabled", "-Dcache-build=disabled"])
    provider = {"macos-arm64": "coretext", "windows-x64": "directwrite", "linux-x64": "fontconfig"}[context.target]
    meson(context, sources, "libass", ["-D" + provider + "=enabled", "-Dlibunibreak=disabled", "-Dasm=enabled"])
    meson(context, sources, "dav1d", ["-Denable_tools=false", "-Denable_tests=false",
        "-Denable_examples=false", "-Denable_docs=false"])
    original = {"libavformat/matroskadec.c": (sources["ffmpeg"] / "libavformat/matroskadec.c").read_bytes()}
    derivatives = ffmpeg_files(original)
    for name, data in derivatives.items():
        (sources["ffmpeg"] / name).write_bytes(data)
    (context.work / "ffmpeg-configuration-derivatives.json").write_text(json.dumps({
        name: {"originalSha256": digest(original[name]), "configuredSha256": digest(data)}
        for name, data in derivatives.items()}, indent=2) + "\n", encoding="utf-8", newline="\n")
    build = context.work / "ffmpeg-build"
    build.mkdir()
    flags = ["--prefix=" + shell_path(context.prefix), "--disable-autodetect", "--disable-programs", "--disable-doc",
        "--disable-network", "--disable-avdevice", "--disable-avfilter", "--disable-encoders", "--disable-muxers",
        "--disable-gpl", "--disable-nonfree", "--disable-version3", "--disable-debug", "--disable-static", "--enable-shared",
        "--cc=" + context.env["CC"], "--cxx=" + context.env["CXX"],
        "--enable-zlib", "--enable-libdav1d", "--extra-cflags=" + context.env["CPPFLAGS"] + " " + context.env["CFLAGS"],
        "--extra-ldflags=" + context.env["LDFLAGS"]]
    if context.target == "macos-arm64":
        flags.extend(["--enable-videotoolbox", "--install-name-dir=@rpath"])
    elif context.target == "windows-x64":
        flags.extend(["--target-os=mingw32", "--arch=x86_64", "--enable-d3d11va", "--pkg-config=pkg-config"])
    context.run("ffmpeg-configure", [shell_path(sources["ffmpeg"] / "configure"), *flags], cwd=build, shell=True)
    context.run("ffmpeg-build", ["make", "-j4"], cwd=build, shell=True)
    context.run("ffmpeg-install", ["make", "install"], cwd=build, shell=True)
