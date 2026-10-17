"""Build and exercise the dependency-free validator as an importable source package."""

import importlib
import json
import os
from pathlib import Path
import shutil
import socket
import sys


def main():
    sources = Path(os.environ["SYNCWATCH_DEPENDENCY_SOURCES"])
    output = Path(os.environ["SYNCWATCH_DEPENDENCY_OUTPUT"])
    flags = json.loads(os.environ["SYNCWATCH_DEPENDENCY_FLAGS"])
    if flags != {"format": "python-source-v1", "version": "2.21.2"}:
        raise ValueError("Unsupported validator source build configuration")
    source = sources / "3rdparty/fastjsonschema/fastjsonschema"
    package = output / "python/fastjsonschema"
    package.mkdir(parents=True)
    for path in sorted(source.glob("*.py")):
        compile(path.read_bytes(), path.name, "exec")
        shutil.copyfile(path, package / path.name)
    sys.path.insert(0, str(package.parent))
    library = importlib.import_module("fastjsonschema")
    assert Path(library.__file__).resolve().is_relative_to(package.resolve())
    assert library.VERSION == flags["version"]

    # This component has no provisioning or runtime network requirement.
    def blocked(*_arguments, **_options):
        raise RuntimeError("The validator source build must remain offline")

    socket.socket = blocked
    root = Path(__file__).resolve().parents[3]
    documents = {json.loads(path.read_text())["$id"]: json.loads(path.read_text()) for path in (root / "contracts/v1").glob("*.schema.json")}
    validators = {}
    for identity, document in documents.items():
        validators[identity] = library.compile(document, handlers={scheme: lambda uri: documents[uri.split("#")[0]] for scheme in ("https", "http", "file", "")}, use_default=False)
    schema = validators["https://schemas.syncwatch.invalid/v1/playback-state.schema.json"]
    fixtures = json.loads((root / "contracts/fixtures/acceptance.json").read_text())
    checked = 0
    # The complete semantic conformance oracle remains tools/contracts/check_contracts.py.
    manifest = json.loads((root / "contracts/manifest.json").read_text())
    for fixture in fixtures:
        if fixture.get("error") is None:
            identity = "https://schemas.syncwatch.invalid/v1/" + Path(manifest["schemas"][fixture["schema"]]).name
            validators[identity](fixture["value"])
            checked += 1
    try:
        schema({"v": 999})
    except library.JsonSchemaValueException:
        pass
    else:
        raise AssertionError("The built validator accepted an invalid wire envelope")
    print(f"Built validator {library.VERSION}: {len(validators)} schemas and {checked} valid acceptance fixtures; no network access")


if __name__ == "__main__":
    main()
