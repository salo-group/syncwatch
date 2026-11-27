"""Build or verify the complete owned headless Syncplay TLS runtime offline."""

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

from source_graph import ROOT, graph_data
from sources import specifications, read_sources, materialize_sources
import python_core
import native_libraries
import crypto_native
import build_sdk
import payload as distribution
import engine_sources
import engine_package
from idna_provenance import verify as verify_idna
from source_files import digest, encoded
from configuration import windows_modules


def inputs(root, target, operation):
    def locate(spec):
        return root / spec["path"] if operation == "build" else root / (Path(spec["path"]).name + ".tar")
    profile = python_core.selected_profile(target)
    core = python_core.read_sources({n: locate(profile[k]) for n, k in
        [("cpython", "source"), ("zlib", "zlib"), ("unicode", "unicode")]}, profile)
    sources = read_sources({n: locate(s) for n, s in specifications().items()})
    engines = engine_sources.read_sources({n: locate(s) for n, s in engine_sources.specifications().items()})
    identity = {"graph": digest(encoded(graph_data())), "python": digest(encoded(profile)),
                "inputs": {n: digest(encoded(s["files"])) for n, s in specifications().items()},
                "engine": {n: digest(encoded(s["files"])) for n, s in engine_sources.specifications().items()}}
    return profile, core, sources, engines, identity


def build(work, output, core, sources, engines, sdk):
    cmake, options, native, rust, perl, env, identity = sdk
    paths = materialize_sources(work / "originals", sources)
    print("Verifying original IDNA tables offline", flush=True)
    provenance = verify_idna(paths, work)
    print("Building owned OpenSSL libraries", flush=True)
    openssl, ssl_commands = native_libraries.openssl(paths["openssl"], work, native, perl, env)
    print("Building owned libffi or the original Windows CFFI port", flush=True)
    ffi, ffi_commands = native_libraries.libffi(paths["libffi"], work, native, env)
    print("Building owned CPython core and selected modules", flush=True)
    profile, source, compiled, core_commands = python_core.build(work / "python", core, openssl, cmake, options, native)
    python = crypto_native.python_sdk(work, profile, core, source, compiled, openssl, native["target"], env)
    backend, cffi_commands = crypto_native.cffi(paths["cffi"], work, python, ffi, cmake, options, native, env)
    print("Building cryptography from the offline Git-only Cargo graph", flush=True)
    extension, crypto_commands = crypto_native.cryptography(paths, work, python, compiled, openssl, backend, rust, native, env)
    engine_files, changes = engine_sources.assemble(engines, work)
    layout = distribution.assemble(output, native["target"], sources, core, profile, compiled, engine_files, source)
    extras = engine_sources.originals(engines) | changes
    for name, spec in specifications().items():
        # Preserve exact original inputs separately from every modified build
        # configuration, including Cargo workspace and cryptography build glue.
        for path, original in sources[name].items():
            configured = paths[name] / path
            if configured.is_file() and configured.read_bytes() != original:
                extras["configuration/dependencies/" + Path(spec["path"]).name + "/" + path] = configured.read_bytes()
    extras["configuration/idna-provenance.json"] = encoded(provenance)
    distribution.write_files(output, extras)
    shutil.copyfile(backend, layout["extensions"] / backend.name)
    destination = layout["extensions"] / "cryptography/hazmat/bindings" / (
        "_rust.pyd" if native["target"] == "windows-x64" else "_rust.abi3.so")
    shutil.copyfile(extension, destination)
    if native["target"] == "windows-x64":
        shutil.copyfile(compiled["runtime"].parent / "python3.dll", layout["extensions"] / "python3.dll")
        ssl_files = list((openssl / "bin").glob("*.dll"))
    else:
        suffix = ".3.dylib" if native["target"] == "macos-arm64" else ".so.3"
        ssl_files = [openssl / "lib" / (n + suffix) for n in ["libssl", "libcrypto"]]
    if len(ssl_files) != 2:
        raise ValueError("Owned OpenSSL runtime library selection differs")
    for path in ssl_files:
        shutil.copyfile(path, layout["extensions"] / path.name)
    return {"openssl": ssl_commands, "libffi": ffi_commands, "python": core_commands,
            "cffi": cffi_commands, "cryptography": crypto_commands}, provenance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("sdk", "build", "verify"))
    parser.add_argument("--platform", choices=("macos-arm64", "windows-x64", "linux-x64"))
    # Explicit diagnostic directory retains failed native builds for local work.
    parser.add_argument("--work", type=Path)
    args = parser.parse_args()
    sdk = build_sdk.resolve()
    cmake, options, native, rust, perl, env, identity = sdk
    if args.operation == "sdk":
        print(json.dumps(identity, sort_keys=True)); return
    if args.platform and args.platform != native["target"]:
        raise ValueError("Headless runtime requires its native target")
    if args.operation == "build":
        if json.loads(os.environ["SYNCWATCH_DEPENDENCY_FLAGS"]) != {"profile": "headless-syncplay-tls-v1"}:
            raise ValueError("Unknown headless runtime configuration")
        root, output = map(Path, (os.environ["SYNCWATCH_DEPENDENCY_SOURCES"], os.environ["SYNCWATCH_DEPENDENCY_OUTPUT"]))
    else:
        root = ROOT / "artifacts/dependencies/syncplay-runtime" / native["target"]
        output = root / "payload"
    profile, core, sources, engines, source_identity = inputs(root, native["target"], args.operation)
    expected_builtins = windows_modules(core["cpython"], profile)[1] if native["target"] == "windows-x64" else ()
    if args.operation == "build":
        def perform(work):
            commands, provenance = build(work, output, core, sources, engines, sdk)
            print("Verifying relocated native startup and real Syncplay TLS exchange", flush=True)
            return engine_package.seal(output, identity, options, source_identity, commands, provenance, work, expected_builtins)
        if args.work:
            args.work.mkdir(parents=True)  # Never overwrite an earlier outcome.
            report = perform(args.work.resolve())
        else:
            work = Path(tempfile.mkdtemp(prefix="native-build-", dir=output.parent)).resolve()
            # Preserve the exact failed build and raw logs for diagnosis. A
            # successful build has its command/log hashes sealed in the receipt.
            report = perform(work)
            shutil.rmtree(work)
    else:
        originals = distribution.originals(sources, core, profile) | engine_sources.originals(engines)
        report = engine_package.verify(output, identity, options, source_identity, originals, expected_builtins, root / "acceptance")
    print(json.dumps({"component": "syncplay-runtime", "target": native["target"],
        "sourceBuiltHeadlessEngine": True, "applicationProviderReady": False,
        "abi": report["proof"]["abi"]["cases"], "engine": report["proof"]["engine"]}, sort_keys=True))


if __name__ == "__main__":
    main()
