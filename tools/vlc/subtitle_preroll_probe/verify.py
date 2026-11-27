# SPDX-License-Identifier: Apache-2.0
"""Verify the aggregate subtitle heap limit against pinned upstream inputs."""

import argparse
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[1] / "native"))

from fixtures import embedded_tracks
from native_context import BuildContext
from source_profile import ROOT, materialize, profile_data, read_snapshot
from subtitle_seek_fixtures import compressed_cues, without_cues


HEAP_LIMIT = 4 * 1024 * 1024
ARCHIVES = ["libavformat/libavformat.a", "libavcodec/libavcodec.a", "libavutil/libavutil.a"]
ZLIB_FILES = ("adler32", "crc32", "inffast", "inflate", "inftrees", "zutil")
MEDIA_INPUT = ROOT / "packages/playback-native"
CRT_ALLOCATIONS = {
    "aligned_alloc", "aligned_free", "aligned_malloc", "aligned_realloc", "calloc", "free", "malloc", "memalign",
    "posix_memalign", "pvalloc", "realloc", "reallocarray", "strdup", "strndup", "valloc",
}
WINDOWS_BINDINGS = ("aligned_malloc", "aligned_realloc", "aligned_free")


class NativeBuild:
    def __init__(self, directory, epoch):
        self.directory = directory
        self.context = BuildContext(directory, epoch, autotools=False)
        self.windows = self.context.target == "windows-x64"
        self.compiler = self.context.env["CC"]
        self.env = self.context.env

    def terminate(self, process):
        if self.windows:
            result = subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                                    capture_output=True, text=True, timeout=15, check=False)
            if result.returncode:
                raise RuntimeError("Could not stop the subtitle build process tree")
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=15)

    def run(self, arguments, timeout=60):
        command = [str(a) for a in arguments]
        if self.windows:
            command = [self.context.shell, "--noprofile", "--norc", "-c", shlex.join(command)]
        trace = self.directory / "command.log"
        try:
            with trace.open("wb") as log:
                flags = subprocess.CREATE_NEW_PROCESS_GROUP if self.windows else 0
                process = subprocess.Popen(command, cwd=self.directory, env=self.env, stdin=subprocess.DEVNULL,
                                           stdout=log, stderr=subprocess.STDOUT, start_new_session=not self.windows,
                                           creationflags=flags)
                try:
                    status = process.wait(timeout=timeout)
                except (subprocess.TimeoutExpired, KeyboardInterrupt):
                    self.terminate(process)
                    raise
            if status:
                raise subprocess.CalledProcessError(status, command)
        except (subprocess.SubprocessError, KeyboardInterrupt, RuntimeError):
            print(trace.read_text(errors="replace")[-4000:], file=sys.stderr)
            raise

    def program(self, name, *arguments):
        executable = self.directory / (name + (".exe" if self.windows else ""))
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
                  "--extra-ldflags=-L.", "--extra-libs=-lswsubtitle"], 420)
        config = (self.directory / "config.h").read_text()
        required = {"CONFIG_ZLIB": 1, "HAVE_PTHREADS": 0, "HAVE_W32THREADS": 0, "HAVE_OS2THREADS": 0,
                    "HAVE_POSIX_MEMALIGN": 0 if self.windows else 1,
                    "HAVE_ALIGNED_MALLOC": 1 if self.windows else 0}
        for name, value in required.items():
            if f"#define {name} {value}" not in config.splitlines():
                raise ValueError(f"FFmpeg configured an unexpected subtitle allocator or thread model: {name}")
        self.run(["make", f"-j{jobs}", *ARCHIVES], 600)
        self.audit_allocations()
        platform_libraries = []
        if self.windows:
            for symbol in WINDOWS_BINDINGS:
                platform_libraries += ["-Wl,--wrap=_" + symbol, "-Wl,--wrap=__imp__" + symbol]
            platform_libraries.append("-lbcrypt")
        self.compile("probe.c", "-I../ffmpeg", "-I.", "-I" + (MEDIA_INPUT / "include").as_posix(),
                     (MEDIA_INPUT / "src/media_input.c").as_posix(), *ARCHIVES, "libz.a", "libswsubtitle.a",
                     "-lm", *platform_libraries, "-o", "subtitle-probe")

    def audit_allocations(self):
        found = set()
        nm = self.context.tool("nm")
        for archive in [*ARCHIVES, "libz.a"]:
            symbols = subprocess.check_output([nm, "-A", "-u", archive], cwd=self.directory, env=self.env,
                                              text=True, timeout=10)
            for line in symbols.splitlines():
                if not line.strip():
                    continue
                reference = line.split()[-1].removeprefix("__imp_").lstrip("_")
                if reference in CRT_ALLOCATIONS:
                    if not (self.windows and reference in WINDOWS_BINDINGS and archive == ARCHIVES[-1]
                            and (":mem.o:" in line or "(mem.o)" in line)):
                        raise ValueError(f"Unbudgeted parser allocation in {archive}: {reference}")
                    found.add(reference)
        if self.windows and found != set(WINDOWS_BINDINGS):
            raise ValueError("Unexpected Windows parser allocator bindings")


def observe(build, path, track, position, limit, expected_start, expected_bytes, *,
            refused=False, read_limited=False, min_expired_cues=0):
    result = json.loads(build.program("subtitle-probe", path, track, position, limit))
    if (result["activeStartMs"] != expected_start or result["captionBytes"] != expected_bytes
            or not 0 < result["peakHeapBytes"] <= limit or result["sourceBytes"] > 512 * 1024
            or result["heapLimited"] != refused or result["readLimited"] != read_limited or result["workLimited"]
            or result["expiredSelectedCues"] < min_expired_cues
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
        build = NativeBuild(scope / "build", profile["sourceDateEpoch"])
        build.compression()
        build.ffmpeg(jobs)
        media = embedded_tracks(scope)
        compressed = scope / "compressed-caption.mkv"
        compressed.write_bytes(compressed_cues(media.read_bytes(), 64, place_in_video_cluster=True))
        missing_cues = scope / "missing-cues.mkv"
        missing_cues.write_bytes(without_cues(indexed_fixture.read_bytes()))
        observations = {
            "overlapping": observe(build, indexed_fixture, 0, 200000, HEAP_LIMIT, 20, 32),
            "otherTrack": observe(build, indexed_fixture, 1, 200000, HEAP_LIMIT, 183180, 32),
            "expired": observe(build, indexed_fixture, 0, 225000, HEAP_LIMIT, -1, 0, min_expired_cues=1),
            "memoryLimit": observe(build, indexed_fixture, 0, 200000, 16384, -1, 0, refused=True),
            "compressed": observe(build, compressed, 0, 1000, HEAP_LIMIT, 0, 84),
            "missingCues": observe(build, missing_cues, 0, 200000, HEAP_LIMIT, -1, 0, read_limited=True),
        }
        for name, specification in originals.items():
            read_snapshot(scope / name, specification)
        return {"target": build.context.target,
                "originalFiles": {name: len(spec["files"]) for name, spec in originals.items()},
                "observations": observations}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", type=Path, help="Verified ffmpeg.tar and zlib.tar from the native source profile")
    parser.add_argument("indexed_fixture", type=Path, help="Authored overlap and independent-track Matroska fixture")
    parser.add_argument("--jobs", type=int, choices=range(1, 5), default=2)
    options = parser.parse_args()
    print(json.dumps(verify(options.sources.resolve(), options.indexed_fixture.resolve(), options.jobs),
                     indent=2, sort_keys=True))


def cancel_build(_signal, _frame):
    raise KeyboardInterrupt


if __name__ == "__main__":
    if os.name != "nt":
        signal.signal(signal.SIGTERM, cancel_build)
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit(130) from None
