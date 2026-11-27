# SPDX-License-Identifier: Apache-2.0
"""Verify the aggregate subtitle heap limit against pinned upstream inputs."""

import argparse
import json
import os
from pathlib import Path
import platform
import shlex
import subprocess
import sys
import tempfile


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from fixtures import embedded_tracks
from source_profile import ROOT, materialize, profile_data, read_snapshot
from subtitle_seek_fixtures import compressed_cues


HEAP_LIMIT = 4 * 1024 * 1024
ARCHIVES = ["libavformat/libavformat.a", "libavcodec/libavcodec.a", "libavutil/libavutil.a"]
ZLIB_FILES = ("adler32", "crc32", "inffast", "inflate", "inftrees", "zutil")
MEDIA_INPUT = ROOT / "packages/playback-native"
CRT_ALLOCATIONS = {
    "aligned_alloc", "aligned_free", "aligned_malloc", "aligned_realloc", "calloc", "free", "malloc", "memalign",
    "posix_memalign", "pvalloc", "realloc", "reallocarray", "strdup", "strndup", "valloc",
}
WINDOWS_BINDINGS = {"aligned_malloc", "aligned_realloc", "aligned_free"}


class NativeBuild:
    def __init__(self, directory):
        self.directory = directory
        self.host = platform.system()
        self.compiler = "cc"
        self.shell = None
        self.env = dict(os.environ, LC_ALL="C")
        if self.host == "Windows":
            msys = Path(os.environ["SYNCWATCH_MSYS_ROOT"])
            self.shell = msys / "usr/bin/bash.exe"
            self.compiler = "gcc"
            self.env.update(MSYSTEM="UCRT64", MSYS2_PATH_TYPE="minimal", CHERE_INVOKING="1")
            self.env["PATH"] = os.pathsep.join([str(msys / "ucrt64/bin"), str(msys / "usr/bin"), self.env["PATH"]])
        elif self.host == "Darwin":
            self.env["MACOSX_DEPLOYMENT_TARGET"] = "13.0"
        elif self.host != "Linux":
            raise ValueError("Unsupported subtitle probe host")

    def run(self, arguments, timeout=60):
        command = [str(a) for a in arguments]
        if self.shell:
            command = [str(self.shell), "--noprofile", "--norc", "-c", shlex.join(command)]
        with (self.directory / "command.log").open("wb") as log:
            result = subprocess.run(command, cwd=self.directory, env=self.env, stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=subprocess.STDOUT, timeout=timeout, check=False)
        if result.returncode:
            print((self.directory / "command.log").read_text(errors="replace")[-4000:], file=sys.stderr)
            result.check_returncode()

    def program(self, name, *arguments):
        executable = self.directory / (name + (".exe" if self.shell else ""))
        return subprocess.check_output([str(executable), *map(str, arguments)], cwd=self.directory,
                                       env=self.env, stdin=subprocess.DEVNULL, text=True, timeout=10).strip()

    def compile(self, source, *arguments):
        self.run([self.compiler, "-O2", "-std=c11", "-Wall", "-Wextra", "-Werror",
                  HERE.as_posix() + "/" + source, *arguments])

    def compression(self):
        self.compile("memory.c", "-c", "-o", "memory.o")
        self.run(["ar", "rcs", "libswsubtitle.a", "memory.o"])
        self.compile("zlib_adapter.c", "-I../zlib", "-c", "-o", "zlib_adapter.o")
        for name in ZLIB_FILES:
            self.run([self.compiler, "-O2", "-DZ_SOLO", "-DZ_PREFIX", "-c", f"../zlib/{name}.c", "-o", name + ".o"])
        # FFmpeg also links ID3 support. Its single-shot decompression calls
        # the same budgeted inflate API from zlib's original uncompr.c.
        self.run([self.compiler, "-O2", "-c", "../zlib/uncompr.c", "-o", "uncompr.o"])
        self.run(["ar", "rcs", "libz.a", "zlib_adapter.o", "uncompr.o", *(name + ".o" for name in ZLIB_FILES)])
        self.compile("memory_test.c", "libswsubtitle.a", "-o", "memory-test")
        self.compile("zlib_test.c", "-I../zlib", "libz.a", "libswsubtitle.a", "-o", "zlib-test")
        print(self.program("memory-test"), file=sys.stderr, flush=True)
        print(self.program("zlib-test"), file=sys.stderr, flush=True)

    def ffmpeg(self, jobs):
        self.run(["sh", "../ffmpeg/configure", "--cc=" + self.compiler, "--disable-programs", "--disable-doc",
                  "--disable-debug", "--disable-everything", "--disable-x86asm", "--disable-asm",
                  "--disable-network", "--disable-autodetect", "--disable-avfilter", "--disable-avdevice",
                  "--disable-swscale", "--disable-swresample", "--disable-pthreads", "--disable-w32threads",
                  "--disable-os2threads", "--enable-demuxer=matroska", "--enable-zlib", "--pkg-config=false",
                  "--malloc-prefix=sw_subtitle_", "--extra-cflags=-I../zlib",
                  "--extra-ldflags=-L.", "--extra-libs=-lswsubtitle"], 300)
        config = (self.directory / "config.h").read_text()
        required = {"CONFIG_ZLIB": 1, "HAVE_PTHREADS": 0, "HAVE_W32THREADS": 0, "HAVE_OS2THREADS": 0,
                    "HAVE_POSIX_MEMALIGN": 0 if self.shell else 1, "HAVE_ALIGNED_MALLOC": 1 if self.shell else 0}
        for name, value in required.items():
            if f"#define {name} {value}" not in config.splitlines():
                raise ValueError(f"FFmpeg configured an unexpected subtitle allocator or thread model: {name}")
        self.run(["make", f"-j{jobs}", *ARCHIVES], 600)
        self.audit_allocations()
        platform_libraries = []
        if self.shell:
            for symbol in ("_aligned_malloc", "_aligned_realloc", "_aligned_free"):
                platform_libraries += ["-Wl,--wrap=" + symbol, "-Wl,--wrap=__imp_" + symbol]
            platform_libraries.append("-lbcrypt")
        self.compile("probe.c", "-I../ffmpeg", "-I.", "-I" + (MEDIA_INPUT / "include").as_posix(),
                     (MEDIA_INPUT / "src/media_input.c").as_posix(), *ARCHIVES, "libz.a", "libswsubtitle.a",
                     "-lm", *platform_libraries, "-o", "subtitle-probe")

    def audit_allocations(self):
        found = set()
        for archive in [*ARCHIVES, "libz.a"]:
            symbols = subprocess.check_output(["nm", "-A", "-u", archive], cwd=self.directory, env=self.env,
                                              text=True, timeout=10)
            for line in symbols.splitlines():
                if not line.strip():
                    continue
                reference = line.split()[-1].removeprefix("__imp_").lstrip("_")
                if reference in CRT_ALLOCATIONS:
                    if not (self.shell and reference in WINDOWS_BINDINGS and archive == ARCHIVES[-1]
                            and (":mem.o:" in line or "(mem.o)" in line)):
                        raise ValueError(f"Unbudgeted parser allocation in {archive}: {reference}")
                    found.add(reference)
        if self.shell and found != WINDOWS_BINDINGS:
            raise ValueError("Unexpected Windows parser allocator bindings")


def observe(build, path, track, position, limit, expected_start, expected_bytes, *, refused=False):
    result = json.loads(build.program("subtitle-probe", path, track, position, limit))
    if (result["activeStartMs"] != expected_start or result["captionBytes"] != expected_bytes
            or not 0 < result["peakHeapBytes"] <= limit or result["sourceBytes"] > 512 * 1024
            or result["heapLimited"] != refused or result["readLimited"] or result["workLimited"]
            or not result["pinnedInputStable"]):
        raise ValueError(f"Unexpected subtitle probe result: {result}")
    return result


def verify(sources, indexed_fixture, jobs):
    profile = profile_data()
    with tempfile.TemporaryDirectory(prefix="sw-subtitle-") as temporary:
        scope = Path(temporary)
        originals = {name: profile["sources"][name] for name in ("ffmpeg", "zlib")}
        for name, specification in originals.items():
            data = read_snapshot(sources / (name + ".tar"), specification)
            materialize(scope / name, data, specification, profile["sourceDateEpoch"])
            del data
        build = NativeBuild(scope / "build")
        build.directory.mkdir()
        build.env["SOURCE_DATE_EPOCH"] = str(profile["sourceDateEpoch"])
        build.compression()
        build.ffmpeg(jobs)
        media = embedded_tracks(scope)
        compressed = scope / "compressed-caption.mkv"
        compressed.write_bytes(compressed_cues(media.read_bytes(), 64, place_in_video_cluster=True))
        observations = {
            "overlapping": observe(build, indexed_fixture, 0, 200000, HEAP_LIMIT, 20, 32),
            "otherTrack": observe(build, indexed_fixture, 1, 200000, HEAP_LIMIT, 183180, 32),
            "expired": observe(build, indexed_fixture, 0, 225000, HEAP_LIMIT, -1, 0),
            "memoryLimit": observe(build, indexed_fixture, 0, 200000, 16384, -1, 0, refused=True),
            "compressed": observe(build, compressed, 0, 1000, HEAP_LIMIT, 0, 84),
        }
        for name, specification in originals.items():
            read_snapshot(scope / name, specification)
        return {"nativeOS": build.host, "originalFiles": {name: len(spec["files"]) for name, spec in originals.items()},
                "observations": observations}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", type=Path, help="Verified ffmpeg.tar and zlib.tar from the native source profile")
    parser.add_argument("indexed_fixture", type=Path, help="Authored overlap and independent-track Matroska fixture")
    parser.add_argument("--jobs", type=int, choices=range(1, 5), default=2)
    options = parser.parse_args()
    print(json.dumps(verify(options.sources.resolve(), options.indexed_fixture.resolve(), options.jobs),
                     indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
