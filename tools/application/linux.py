"""Assemble the Kotlin application without borrowing a developer JVM or classpath."""

from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import zipfile

from candidate_tree import Component
from receipts import ROOT, clients, desktop_classpath, jvm, renderer, require_host
from runtime_media import qualify
import account_store_linux


def prepare(arguments, work):
    require_host("linux-x64")
    runtime = jvm(arguments.jvm)
    client_receipt = clients(arguments.linux_clients)
    compatibility = qualify(arguments.media_package, arguments.runtime_build_media, runtime, client_receipt)
    desktop = arguments.desktop_sdk.resolve(strict=True)
    classpath = desktop_classpath(desktop)
    renderer_receipt = renderer(arguments.renderer)
    relative = ["Desktop/" + path.resolve(strict=True).relative_to(desktop).as_posix() for path in classpath]
    jar = arguments.application.resolve(strict=True)
    application_receipt = json.loads(jar.with_name("application-report.json").read_text())
    with jar.open("rb") as data:
        if (application_receipt.get("schemaVersion") != 1 or application_receipt.get("target") != "linux-x64"
                or application_receipt.get("testSourcesIncluded") is not False
                or application_receipt.get("sha256") != hashlib.file_digest(data, "sha256").hexdigest()
                or not application_receipt.get("jdkModules")
                or set(application_receipt["jdkModules"]) - {"java.base", *runtime["modules"]}):
            raise ValueError("Linux application build receipt or JVM module closure differs")
    with zipfile.ZipFile(jar) as archive:
        if "syncwatch/MainKt.class" not in archive.namelist():
            raise ValueError("Application JAR does not contain the native Linux entry")
    application = work / "application"
    application.mkdir()
    if compatibility is not None:
        (application / "runtime-media-compatibility.json").write_text(json.dumps(compatibility.report, indent=2) + "\n")
        (application / "runtime-build-media-report.json").write_bytes(compatibility.build_receipt)
    shutil.copy2(jar, application / "application.jar")
    shutil.copy2(jar.with_name("application-report.json"), application / "application-report.json")
    for name in ("syncwatch_playback.so", "syncwatch_playback_jni.so"):
        shutil.copy2(arguments.bridge / name, application / name)
    shutil.copy2(ROOT / "LICENSE", application / "LICENSE")
    account_store_linux.build(application / "syncwatch-account-store", arguments.renderer, arguments.cc)
    options = ["--enable-native-access=ALL-UNNAMED", "-Dskiko.library.path=Renderer/runtime",
               "-cp", "application.jar:" + ":".join(relative), "syncwatch.MainKt"]
    (application / "launch.args").write_text("\n".join('"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"' for value in options) + "\n", encoding="utf-8")
    subprocess.run([arguments.cc, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                    str(ROOT / "tools/application/launcher_linux.c"), "-o", str(application / "SyncWatch")],
                   check=True, timeout=30)
    # Ship runtime libraries, data and correspondence; native compiler/tools/objects are SDK-only.
    rendering = work / "renderer"
    rendering.mkdir()
    files = {}
    for name, expected in renderer_receipt["files"].items():
        if name.startswith(("runtime/", "share/", "notices/")) or name in {"SCOPE.txt", "THIRD-PARTY-NOTICES.txt", "sources.zip"}:
            source = arguments.renderer / name
            target = rendering / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            with target.open("rb") as data:
                if hashlib.file_digest(data, "sha256").hexdigest() != expected:
                    raise ValueError("Renderer source bytes changed during staging")
            files[name] = expected
    (rendering / "renderer-report.json").write_text(json.dumps({"schemaVersion": 1, "target": "linux-x64",
        "profile": renderer_receipt["profile"], "files": files}, indent=2) + "\n")
    return [Component(application, "SyncWatch"), Component(arguments.jvm, "SyncWatch/Runtime"),
            Component(arguments.linux_clients, "SyncWatch/Clients"),
            Component(desktop, "SyncWatch/Desktop"), Component(rendering, "SyncWatch/Renderer"),
            Component(arguments.media_package, "SyncWatch/Media"), Component(arguments.helper_package, "SyncWatch/Syncplay")]
