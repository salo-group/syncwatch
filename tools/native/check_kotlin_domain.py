"""Compile the Linux Kotlin domain against owned runtime JARs on Linux."""

import json
import os
from pathlib import Path
import shutil
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/jvm"))
import annotations
import jdk
import kotlin
import stdlib
from build_support import digest


def check(payload, output, *, toolchain_root=ROOT):
    if sys.platform != "linux":
        raise SystemExit("Linux Kotlin client checks require Linux")
    home, compiler = jdk.home(toolchain_root), kotlin.home(toolchain_root)
    if output.exists():
        shutil.rmtree(output)
    classes = output / "classes"
    classes.mkdir(parents=True)
    java_sources = sorted((ROOT / "packages/ipc/jvm").rglob("*.java"))
    stdlib.javac(["--release", "17", "-encoding", "UTF-8", "-proc:none", "-Xlint:all", "-Werror",
                  "-d", str(classes), *map(str, java_sources)], output, home)
    binary = (payload / stdlib.BINARY).resolve()
    kotlin_sources = sorted((ROOT / "packages/linux-domain/src").rglob("*.kt"))
    kotlin_sources += sorted((ROOT / "packages/linux-domain/tests").rglob("*.kt"))
    kotlin.compile(["-no-stdlib", "-no-reflect", "-jdk-home", str(home), "-classpath", os.pathsep.join(map(str, [classes, binary])),
                    "-jvm-target", "17", "-language-version", "2.3", "-api-version", "2.3", "-Werror",
                    "-d", str(classes), *map(str, kotlin_sources)], ROOT, home, compiler)
    transcript = kotlin.java(["-cp", os.pathsep.join(map(str, [classes, binary, payload / "annotations" / annotations.BINARY])),
                              "syncwatch.domain.DomainChecksKt", str(ROOT / "contracts/fixtures/local-domain.json")], ROOT, home)
    if "SYNCWATCH_LINUX_DOMAIN_OK" not in transcript:
        raise ValueError("Kotlin domain did not complete the shared contract corpus")
    report = {"schemaVersion": 1, "hostTarget": jdk.current_target(), "kotlinDomainCompiled": True,
              "sharedDomainAccepted": True, "stateCases": 31, "lifecycleScenarios": 7,
              "nativeActionScenarios": len(json.loads((ROOT / "contracts/fixtures/playback-authority.json").read_text())["cases"]),
              "checkpointScenarios": len(json.loads((ROOT / "contracts/fixtures/shared-playback-checkpoint.json").read_text())["cases"]),
              "mediaAgreementScenarios": len(json.loads((ROOT / "contracts/fixtures/media-agreement.json").read_text())["cases"]),
              "applicationBuilt": False, "guiAccepted": False, "physicalOutputAccepted": False,
              "transcript": transcript.strip(),
              "sourceFiles": {p.relative_to(ROOT).as_posix(): digest(p.read_bytes()) for p in
                              [*java_sources, *kotlin_sources, ROOT / "contracts/fixtures/local-domain.json",
                               ROOT / "contracts/fixtures/playback-authority.json", ROOT / "contracts/fixtures/media-agreement.json",
                               ROOT / "contracts/fixtures/shared-playback-checkpoint.json"]},
              "runtimeSha256": digest(binary.read_bytes())}
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(transcript.strip())
    return report


def main():
    if sys.platform != "linux":
        raise SystemExit("Linux Kotlin client checks require Linux")
    payload = ROOT / "artifacts/dependencies/kotlin-stdlib" / jdk.current_target() / "payload"
    # Verify cached runtime/source correspondence before executing any application code.
    import subprocess
    subprocess.run([sys.executable, str(ROOT / "tools/jvm/stdlib.py"), "verify"], cwd=ROOT, check=True)
    check(payload.resolve(), ROOT / "artifacts/kotlin-domain" / jdk.current_target())


if __name__ == "__main__":
    main()
