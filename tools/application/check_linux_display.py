"""Check redraw and recovery on a private Linux X server with the normal launcher."""

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import select
import signal
import subprocess
import sys
import tempfile

from receipts import ROOT, checked_receipt, desktop_classpath, require_host


def stop(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    finally:
        # The process leader can exit before its last child.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


def run(command, *, timeout, env=None):
    with subprocess.Popen(command, env=env, start_new_session=True) as process:
        try:
            status = process.wait(timeout=timeout)
        except BaseException:
            stop(process)
            raise
        if status:
            raise subprocess.CalledProcessError(status, command)


@contextmanager
def private_display(directory, executable):
    authority = directory / "Xauthority"
    # An Xauthority FamilyWild record authorizes this private cookie before
    # Xvfb allocates its own display number. The temporary directory is private.
    with authority.open("xb") as file:
        authority.chmod(0o600)
        file.write(b"\xff\xff\x00\x00\x00\x00\x00\x12MIT-MAGIC-COOKIE-1\x00\x10" + secrets.token_bytes(16))
    read, write = os.pipe()
    server = None
    number = None
    try:
        try:
            server = subprocess.Popen([str(executable), "-displayfd", str(write), "-nolisten", "tcp", "-noreset",
                                       "-screen", "0", "1024x768x24", "-auth", str(authority)],
                                      pass_fds=(write,), stdin=subprocess.DEVNULL, start_new_session=True)
        finally:
            os.close(write)
        if not select.select([read], [], [], 8)[0]:
            raise RuntimeError("Private X display did not start")
        number = os.read(read, 32).strip().decode("ascii")
        if not number.isdecimal() or server.poll() is not None:
            raise RuntimeError("Xvfb did not allocate a private display")
        runtime = directory / "runtime"
        runtime.mkdir(mode=0o700)
        yield {"DISPLAY": ":" + number, "XAUTHORITY": str(authority), "XDG_RUNTIME_DIR": str(runtime),
               "XDG_CACHE_HOME": str(directory / "cache"), "PATH": os.defpath}
    finally:
        os.close(read)
        if server is not None and server.returncode is None:
            stop(server)
        if number is not None and number.isdecimal():
            if Path(f"/tmp/.X{number}-lock").exists() or Path(f"/tmp/.X11-unix/X{number}").exists():
                raise RuntimeError("Private X display resources were retained")


def check(arguments):
    require_host("linux-x64")
    desktop = desktop_classpath(arguments.desktop_sdk)
    renderer = arguments.renderer.resolve(strict=True)
    checked_receipt(renderer, "renderer-report.json")
    home = arguments.jdk.resolve(strict=True)
    sources = Path(__file__).resolve().parent
    sys.path.insert(0, str(ROOT / "tools/jvm"))
    import jdk
    from build_support import java_environment
    from kotlin import argument_file

    spec = json.loads((ROOT / "tools/jvm/toolchains.json").read_text(encoding="utf-8"))
    if jdk.verify(home.parent, spec, "linux-x64") != home:
        raise ValueError("Display probe requires the pinned Linux developer JDK")

    with tempfile.TemporaryDirectory(prefix="syncwatch-display-") as temporary:
        directory = Path(temporary)
        classes = directory / "classes"
        classes.mkdir()
        classpath = os.pathsep.join(map(str, desktop))
        run([str(home / "bin/javac"), "-proc:none", "-cp", classpath, "-d", str(classes),
             str(sources / "tests/LinuxDisplayProbe.java")], env=java_environment(), timeout=30)
        (directory / "Runtime").symlink_to(home, target_is_directory=True)
        (directory / "Renderer").symlink_to(renderer, target_is_directory=True)
        launcher = directory / "SyncWatch"
        run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", str(sources / "launcher_linux.c"),
             "-o", str(launcher)], timeout=20)
        argument_file(["-Djava.awt.headless=false", "-Dskiko.library.path=Renderer/runtime",
                       "-Dskiko.data.path=" + str(directory / "skiko"), "-Duser.home=" + str(directory),
                       "-Dskiko.renderApi=SOFTWARE_FAST", "-cp", str(classes) + os.pathsep + classpath,
                       "LinuxDisplayProbe"], directory / "launch.args")
        with private_display(directory, arguments.xvfb.resolve(strict=True)) as environment:
            run([str(launcher)], env=environment, timeout=30)


def interrupted(signum, _frame):
    signal.signal(signum, signal.SIG_IGN)
    raise SystemExit(128 + signum)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("desktop-sdk", "renderer", "jdk", "xvfb"):
        parser.add_argument("--" + name, type=Path, required=True)
    check(parser.parse_args())
