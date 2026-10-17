"""Validate versioned contracts and acceptance fixtures using pinned local sources."""

import argparse
from dataclasses import dataclass, field
import importlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tomllib
import unicodedata
from urllib.parse import urldefrag


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_BASE = "https://schemas.syncwatch.invalid/v1/"
INTEGER_FIELDS = {
    "v", "mediaGeneration", "watchGeneration", "sequence", "positionMs",
    "durationMs", "fileSizeBytes", "port",
}


class ContractError(ValueError):
    """A bounded, machine-readable contract rejection without echoed input."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def reject(condition: bool, code: str) -> None:
    if condition:
        raise ContractError(code)


def strict_json(data: bytes) -> object:
    def pairs(items):
        result = {}
        for key, value in items:
            reject(key in result, "malformed")
            result[key] = value
        return result

    def number(value):
        result = float(value)
        reject(not math.isfinite(result), "malformed")
        return result

    def invalid_constant(_):
        raise ContractError("malformed")

    try:
        return json.loads(
            data.decode("utf-8"), object_pairs_hook=pairs,
            parse_float=number, parse_constant=invalid_constant,
        )
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ContractError("malformed") from error


def check_tree(value: object, depth: int = 0, limit: int = 32) -> None:
    reject(depth > limit, "malformed")
    if isinstance(value, dict):
        for key, child in value.items():
            reject(not isinstance(key, str), "malformed")
            # Wire integers use integer tokens, avoiding floating-point rounding at bounds.
            if key in INTEGER_FIELDS and child is not None:
                reject(type(child) is not int, "malformed")
            check_tree(key, depth + 1, limit)
            check_tree(child, depth + 1, limit)
    elif isinstance(value, list):
        for child in value:
            check_tree(child, depth + 1, limit)
    elif isinstance(value, str):
        reject(any(0xD800 <= ord(char) <= 0xDFFF for char in value), "malformed")
    elif type(value) is float:
        reject(not math.isfinite(value), "malformed")
    else:
        reject(type(value) not in {int, bool, type(None)}, "malformed")


@dataclass(frozen=True)
class Context:
    """Trusted receiver state; never take these values from an incoming frame."""

    clientRunId: str | None = None
    mediaGeneration: int | None = None
    roomEpoch: str | None = None
    watchGeneration: int | None = None
    sequence: int | None = None
    members: dict[str, str] = field(default_factory=dict)


class Registry:
    def __init__(self, root: Path = ROOT):
        self.root = root.resolve()
        self.directory = self.root / "contracts"
        self.manifest = strict_json((self.directory / "manifest.json").read_bytes())
        config = tomllib.loads((self.root / "tools/contracts/pyproject.toml").read_text())
        dependency = config["tool"]["syncwatch"]["source-dependencies"]["fastjsonschema"]
        source = self.root / dependency["path"]
        if not (source / "fastjsonschema/__init__.py").is_file():
            raise ValueError("Initialize 3rdparty/fastjsonschema before running contract checks")
        revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        if revision != dependency["revision"]:
            raise ValueError("Contract validator source revision does not match its manifest")
        entry = subprocess.check_output(
            ["git", "ls-files", "--stage", "--", dependency["path"]], cwd=self.root, text=True,
        ).split()
        if len(entry) < 3 or entry[:3] != ["160000", revision, "0"]:
            raise ValueError("Contract validator source pin must match the Git index")
        sys.path.insert(0, str(source))
        library = importlib.import_module("fastjsonschema")
        if not Path(library.__file__).resolve().is_relative_to(source.resolve()):
            raise ValueError("Contract validator must load from the declared source submodule")
        if library.VERSION != dependency["version"]:
            raise ValueError("Contract validator source version does not match its manifest")
        self.schema_error = library.JsonSchemaValueException
        self.documents = {}
        for path in sorted((self.directory / "v1").glob("*.schema.json")):
            document = strict_json(path.read_bytes())
            if document.get("$id") != SCHEMA_BASE + path.name:
                raise ValueError("Schema identity must match its versioned local path")
            if document.get("$schema") != self.manifest["schemaDialect"]:
                raise ValueError("Schema dialect must match the contract manifest")
            self.documents[document["$id"]] = document
        for document in self.documents.values():
            self.check_references(document)
        handlers = {scheme: self.resolve for scheme in ("https", "http", "file", "")}
        self.validators = {}
        for name, relative in self.manifest["schemas"].items():
            path = (self.directory / relative).resolve()
            if not path.is_relative_to(self.directory) or not path.is_file():
                raise ValueError("Contract entry must resolve to a local schema")
            document = self.resolve(SCHEMA_BASE + path.name)
            self.validators[name] = library.compile(document, handlers=handlers, use_default=False)

    def resolve(self, uri: str) -> dict:
        identity, _ = urldefrag(uri)
        if identity not in self.documents:
            raise ValueError("Schema reference is not in the offline registry")
        return self.documents[identity]

    def check_references(self, value: object) -> None:
        if isinstance(value, dict):
            if "$ref" in value:
                # Only absolute registered identities are used, with optional JSON pointers.
                self.resolve(value["$ref"])
            for child in value.values():
                self.check_references(child)
        elif isinstance(value, list):
            for child in value:
                self.check_references(child)

    def decode_frame(self, frame: bytes) -> object:
        reject(len(frame) > self.manifest["maxFrameBytes"], "frameTooLarge")
        reject(not frame.endswith(b"\n") or b"\n" in frame[:-1] or b"\r" in frame, "malformed")
        value = strict_json(frame[:-1])
        check_tree(value, limit=self.manifest["maxNesting"])
        return value

    def validate(self, kind: str, value: object) -> None:
        """Check structure and cross-field invariants; this does not authorize receipt."""
        reject(kind not in self.validators, "malformed")
        check_tree(value, limit=self.manifest["maxNesting"])
        reject(not isinstance(value, dict), "malformed")
        reject(type(value.get("v")) is not int, "malformed")
        reject(value["v"] != 1, "unsupportedVersion")
        try:
            self.validators[kind](value)
        except self.schema_error as error:
            raise ContractError("malformed") from error
        if kind == "playback-state":
            self.check_playback(value)
        elif kind == "media-descriptor":
            self.check_media(value)
        elif kind == "helper-ipc":
            if value["type"] == "player.state":
                self.check_playback(value["payload"])
            elif value["type"] == "bridge.hello":
                reject(any(type(version) is not int for version in value["payload"]["supportedVersions"]), "malformed")
            events = {"player.state", "sync.connection", "sync.participants", "sync.readiness"}
            if value["type"] not in events:
                command = value.get("replyTo", value.get("id"))
                reject(int(command[4:]) > 9007199254740991, "malformed")
        elif kind == "room-snapshot":
            ids = [member["memberId"] for member in value["members"]]
            usernames = [member["syncplayUsername"] for member in value["members"]]
            reject(len(set(ids)) != len(ids) or len(set(usernames)) != len(usernames), "conflict")
        elif kind == "room-event" and value["type"] == "member.bound":
            payload = value["payload"]
            reject(payload["memberId"] != value["memberId"] or payload["clientRunId"] != value["clientRunId"], "conflict")
        elif kind == "room-event" and value["type"] == "catalog.updated":
            ids = [item["mediaId"] for item in value["payload"]["items"]]
            reject(len(ids) != len(set(ids)), "conflict")
            for item in value["payload"]["items"]:
                self.check_media(item)

    @staticmethod
    def check_media(value: dict) -> None:
        title = unicodedata.normalize("NFC", value["title"])
        key = " ".join(part for part in title.split(" ") if part)
        key = key.translate(str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"))
        reject(not key or value["titleKey"] != key, "invalidState")

    @staticmethod
    def check_playback(value: dict) -> None:
        duration = value["durationMs"]
        reject(duration is not None and value["positionMs"] > duration, "invalidState")
        if value["status"] in {"unloaded", "loading", "failed"}:
            reject(not value["paused"] or value["seekable"] or value["rate"] != 1, "invalidState")
        if value["status"] == "unloaded":
            reject(value["positionMs"] != 0 or duration is not None, "invalidState")

    def accept(self, kind: str, value: object, context: Context) -> None:
        """Validate receipt against trusted state without mutating that state."""
        self.validate(kind, value)
        if kind in {"helper-ipc", "playback-state"}:
            reject(context.clientRunId is None or context.mediaGeneration is None, "invalidState")
            reject(value["clientRunId"] != context.clientRunId, "foreignRun")
            reject(value["mediaGeneration"] != context.mediaGeneration, "staleGeneration")
        elif kind in {"watch-generation", "room-event"}:
            reject(context.roomEpoch is None or context.watchGeneration is None, "invalidState")
            reject(value["roomEpoch"] != context.roomEpoch, "staleEpoch")
            advances = kind == "room-event" and value["type"] in {"selection.changed", "watch.started", "watch.ended"}
            expected = context.watchGeneration + int(advances)
            reject(value["watchGeneration"] != expected, "staleGeneration")
            if kind == "room-event":
                reject(context.sequence is None, "invalidState")
                reject(value["sequence"] <= context.sequence, "duplicate")
                reject(value["sequence"] != context.sequence + 1, "sequenceGap")
                # New/rebound membership is accepted only as a server-ordered binding event.
                if value["type"] != "member.bound":
                    reject(context.members.get(value["memberId"]) != value["clientRunId"], "foreignRun")


def check_fixtures(registry: Registry) -> int:
    fixtures = strict_json((registry.directory / "fixtures/acceptance.json").read_bytes())
    seen = set()
    covered = set()
    for case in fixtures:
        name, kind = case["name"], case["schema"]
        if name in seen:
            raise ValueError("Acceptance case names must be unique")
        seen.add(name)
        covered.add(kind)
        actual = None
        try:
            value = registry.decode_frame(case["frame"].encode("utf-8")) if "frame" in case else case["value"]
            if "context" in case:
                registry.accept(kind, value, Context(**case["context"]))
            else:
                registry.validate(kind, value)
        except ContractError as error:
            actual = error.code
        if actual != case["error"]:
            raise ValueError(f"Acceptance case {name}: expected {case['error']}, received {actual}")
    if covered != set(registry.validators):
        raise ValueError("Acceptance fixtures must cover every contract entry")
    return len(fixtures)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        registry = Registry(args.root)
        count = check_fixtures(registry)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Contract checks failed: {error}", file=sys.stderr)
        return 1
    print(f"Validated {len(registry.validators)} contracts and {count} acceptance fixtures offline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
