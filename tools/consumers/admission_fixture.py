"""Own a loopback peer that stalls events while its frozen selection stays available."""

from contextlib import contextmanager
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[2]
VARIABLE = "SYNCWATCH_ADMISSION_ISOLATION_FIXTURE"


@contextmanager
def environment(root=ROOT):
    corpus = json.loads((root / "contracts/fixtures/room-client.json").read_text(encoding="utf-8"))
    admission = copy.deepcopy(json.loads(corpus["admissions"][0]["wire"]))
    admission["expiresAt"] = "2099-01-01T00:00:00Z"
    snapshot = admission["bootstrap"]["snapshot"]
    snapshot["watchGeneration"] = 2
    snapshot["watch"] = {"status": "active", "mediaId": "movie"}
    selection = {"v": 1, "roomEpoch": snapshot["roomEpoch"], "sequence": 8, "watchGeneration": 2,
                 "selection": {"memberId": admission["memberId"], "media": {
                     "v": 1, "mediaId": "movie", "title": "Movie", "titleKey": "movie", "fileSizeBytes": 1000,
                     "durationMs": 60000, "fullHashSha256": "a" * 64}}}
    invalid = [copy.deepcopy(admission["bootstrap"]) for _ in range(3)]
    invalid[0]["snapshot"]["roomEpoch"] = "foreign-epoch"
    invalid[1]["connection"]["host"] = "foreign.example.com"
    invalid[2]["snapshot"]["members"][0]["clientRunId"] = "foreign-run"
    advanced = copy.deepcopy(admission)
    advanced["bootstrap"]["snapshot"]["sequence"] = 9
    stopped = threading.Event()
    with tempfile.TemporaryDirectory(prefix="sw-admission-isolation-") as temporary:
        directory = Path(temporary)

        class Peer(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(5)

            def log_message(self, *_arguments):
                pass

            def do_GET(self):
                path = urlsplit(self.path).path
                if path.endswith("/events"):
                    (directory / "held").touch()
                    stopped.wait(30)
                    self.close_connection = True
                    return
                if path.endswith("/selection"):
                    data = json.dumps(selection).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                # A bootstrap would replace a real server's catalog cursor.
                (directory / "unexpected-bootstrap").touch()
                self.send_error(409)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Peer)
        thread = threading.Thread(target=server.serve_forever, name="owned-admission-fixture")
        previous = os.environ.get(VARIABLE)
        config = directory / "fixture.json"
        config.write_text(json.dumps({"service": "http://127.0.0.1:" + str(server.server_port),
            "identity": corpus["identity"], "admission": admission, "held": str(directory / "held"),
            "invalidAnchors": invalid, "advancedAdmission": advanced,
            "unexpectedBootstrap": str(directory / "unexpected-bootstrap")}), encoding="utf-8")
        try:
            thread.start()
            os.environ[VARIABLE] = str(config)
            yield
            if (directory / "unexpected-bootstrap").exists():
                raise AssertionError("Admission read attempted to replace the catalog cursor")
        finally:
            if previous is None:
                os.environ.pop(VARIABLE, None)
            else:
                os.environ[VARIABLE] = previous
            stopped.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            if thread.is_alive():
                raise AssertionError("Owned admission fixture did not stop")


def run_native(command):
    options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    process = subprocess.Popen(command, **options)
    try:
        return process.wait(timeout=180)
    finally:
        if process.poll() is None:
            if os.name == "nt":
                stopped = subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                if stopped.returncode and process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)
                    raise RuntimeError("Owned native test tree did not stop")
            else:
                os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)


if __name__ == "__main__":
    command = sys.argv[1:]
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        raise SystemExit("Pass a native test command after --")
    with environment():
        result = run_native(command)
    raise SystemExit(result)
