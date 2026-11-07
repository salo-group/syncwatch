"""Verify the current Linux application against an already validated desktop SDK."""

import json
import os
from pathlib import Path
import sys
import tempfile

import kotlin
import stdlib
from admission_fixture import environment


def check(root, classpath, home, compiler):
    if sys.platform != "linux":
        raise SystemExit("Linux application/design consumer requires Linux")
    with environment(root):
        _check(root, classpath, home, compiler)


def _check(root, classpath, home, compiler):
    with tempfile.TemporaryDirectory(prefix="compose-ui-consumer-") as temporary:
        work = Path(temporary).resolve() / "Кино 映画"
        work.mkdir()
        classes = work / "classes"
        classes.mkdir()
        manifest = json.loads((root / "apps/linux/application.json").read_text())
        sources = sorted(path for source in manifest["sources"] for path in (root / source).rglob("*")
                         if path.suffix in {".java", ".kt"})
        sources += sorted((root / "packages/design/tests/linux").glob("*.kt"))
        sources += sorted((root / "packages/linux-playback/tests").rglob("*.kt"))
        sources += sorted((root / "packages/linux-library/tests").rglob("*.kt"))
        sources += sorted((root / "apps/linux/tests").rglob("*.kt"))
        sources += sorted((root / "packages/linux-room/tests").rglob("*.kt"))
        sources.append(root / "tools/jvm/tests/DisplayRefreshChecks.kt")
        stdlib.javac(["--release", "21", "-g:none", "-proc:none", "-encoding", "UTF-8", "-cp",
                      os.pathsep.join(map(str, classpath)), "-d", str(classes),
                      *[str(path) for path in sources if path.suffix == ".java"]], work, home)
        kotlin.compile(["-jvm-target", "21", "-Werror", "-classpath", os.pathsep.join(map(str, [classes, *classpath])),
                        "-d", str(classes), *[str(path) for path in sources if path.suffix == ".kt"]],
                       work, home, compiler, plugins=("compose",))
        output = kotlin.java(["-Djava.awt.headless=true", "-cp", os.pathsep.join(map(str, [classes, *classpath])),
                              "syncwatch.design.tests.PresentationChecksKt", str(root / "packages/design/tests/fixtures")], work, home)
        if "SYNCWATCH_DESIGN_PRESENTATIONS_OK" not in output:
            raise ValueError("Desktop UI first-party application/design consumer failed")
        print(output.strip())
        output = kotlin.java(["-Djava.awt.headless=true", "-cp", os.pathsep.join(map(str, [classes, *classpath])),
                              "syncwatch.tests.DisplayRefreshChecksKt"], work, home)
        if "SYNCWATCH_DISPLAY_REFRESH_OK" not in output:
            raise ValueError("Display refresh fallback/frame scheduling consumer failed")
        print(output.strip())
        output = kotlin.java(["-Djava.awt.headless=true", "-cp", os.pathsep.join(map(str, [classes, *classpath])),
                              "syncwatch.bridge.PlayerCorrectionChecksKt", str(root / "contracts/fixtures/helper-player.json")], work, home)
        if "SYNCWATCH_HELPER_PLAYER_OK" not in output:
            raise ValueError("Helper/player wire boundary consumer failed")
        print(output.strip())
        output = kotlin.java(["-Djava.awt.headless=true", "-cp", os.pathsep.join(map(str, [classes, *classpath])),
                              "syncwatch.playback.KotlinPortChecksKt"], work, home)
        if "SYNCWATCH_KOTLIN_PLAYBACK_OK" not in output:
            raise ValueError("Kotlin playback value/cancellation consumer failed")
        print(output.strip())
        output = kotlin.java(["-Djava.awt.headless=true", "-cp", os.pathsep.join(map(str, [classes, *classpath])),
                              "syncwatch.library.LibraryChecksKt", str(root / "contracts/fixtures/library-catalog.json")], work, home)
        if "SYNCWATCH_LIBRARY_OK" not in output:
            raise ValueError("Local filesystem library consumer failed")
        print(output.strip())
        output = kotlin.java(["-Djava.awt.headless=true", "-cp", os.pathsep.join(map(str, [classes, *classpath])),
                              "syncwatch.ApplicationLibraryChecksKt", str(root)], work, home)
        if "SYNCWATCH_APPLICATION_LIBRARY_OK" not in output:
            raise ValueError("Application library ownership/cancellation consumer failed")
        print(output.strip())
        output = kotlin.java(["-cp", os.pathsep.join(map(str, [classes, *classpath])),
                              "syncwatch.room.RoomProtocolChecksKt", str(root / "contracts/fixtures/room-client.json")], work, home)
        if "SYNCWATCH_ROOM_PROTOCOL_OK" not in output:
            raise ValueError("Room admission protocol consumer failed")
        print(output.strip())
