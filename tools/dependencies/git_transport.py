"""Retry only transient server failures while preserving exact Git operations."""

import re
import subprocess
import sys
import time


RETRY_DELAYS = (1, 2)
SERVER_ERROR = re.compile(rb"(?:URL returned error:|HTTP)\s+(?:500|502|503|504)\b|curl\s+92\s+HTTP/2\b", re.I)
PERMANENT_ERROR = re.compile(
    rb"URL returned error:\s*(?:400|401|403|404|410|422)\b|not our ref|couldn't find remote ref|"
    rb"authentication failed|permission denied|object hash mismatch|repository not found|"
    rb"not a valid object|invalid object|bad object|unknown revision|invalid reference", re.I)


def emit_error(data):
    if not data:
        return
    # Git emits UTF8 independently of Python's Windows text code page.
    buffer = getattr(sys.stderr, "buffer", None)
    if buffer is not None:
        buffer.write(data)
        buffer.flush()
    else:
        sys.stderr.write(data.decode("utf-8", errors="replace"))


def run(arguments, *, cwd, input=None, capture_output=False):
    """Never change a URL, pin or checkout policy when retrying a server error."""
    command = ["git", *arguments]
    data = input.encode("utf-8") if isinstance(input, str) else input
    for attempt in range(len(RETRY_DELAYS) + 1):
        result = subprocess.run(command, cwd=cwd, input=data,
                                stdout=subprocess.PIPE if capture_output else None,
                                stderr=subprocess.PIPE, check=False)
        emit_error(result.stderr)
        if result.returncode == 0:
            return result
        error = result.stderr or b""
        if (result.returncode not in (1, 128) or PERMANENT_ERROR.search(error) or
                not SERVER_ERROR.search(error) or attempt == len(RETRY_DELAYS)):
            raise subprocess.CalledProcessError(result.returncode, command, output=result.stdout, stderr=result.stderr)
        print(f"Dependency Git: transient HTTP failure, attempt {attempt + 1}/{len(RETRY_DELAYS) + 1}; retrying identical arguments",
              file=sys.stderr, flush=True)
        time.sleep(RETRY_DELAYS[attempt])
