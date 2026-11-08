"""Partition the validated dependency matrix without changing selected builds."""

import json
import os
import re
import sys


PLATFORMS = {"linux-x64": "linux", "macos-arm64": "macos", "windows-x64": "windows"}


def partition(matrix):
    if (not isinstance(matrix, dict) or set(matrix) != {"include"}
            or not isinstance(matrix["include"], list) or not 0 < len(matrix["include"]) <= 256):
        raise ValueError("Dependency selection must contain a bounded matrix")
    result = {name: {"include": []} for name in PLATFORMS.values()}
    seen = set()
    for entry in matrix["include"]:
        if (not isinstance(entry, dict) or set(entry) != {"component", "platform", "cold"}
                or not isinstance(entry["component"], str) or not re.fullmatch(r"[a-z][a-z0-9-]*", entry["component"])
                or not isinstance(entry["platform"], str) or entry["platform"] not in PLATFORMS
                or type(entry["cold"]) is not bool):
            raise ValueError("Dependency selection contains an invalid build")
        identity = (entry["component"], entry["platform"])
        if identity in seen:
            raise ValueError("Dependency selection contains a duplicate build")
        seen.add(identity)
        result[PLATFORMS[entry["platform"]]]["include"].append(entry)
    if any(not matrix["include"] for matrix in result.values()):
        raise ValueError("Dependency selection must retain every platform baseline")
    return result


def main():
    try:
        result = partition(json.loads(os.environ["CI_DEPENDENCY_MATRIX"]))
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            for name, matrix in result.items():
                output.write(name + "=" + json.dumps(matrix, separators=(",", ":")) + "\n")
    except (KeyError, OSError, ValueError) as error:
        print(f"Dependency partition failed: {error}", file=sys.stderr)
        return 1
    print("Every selected dependency retained in its platform matrix")
    return 0


if __name__ == "__main__":
    sys.exit(main())
