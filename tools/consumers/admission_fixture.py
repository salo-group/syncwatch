"""Own a loopback peer that stalls events while its frozen selection stays available."""

from contextlib import contextmanager
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[2]
VARIABLE = "SYNCWATCH_ADMISSION_ISOLATION_FIXTURE"


class OwnedServer(ThreadingHTTPServer):
    daemon_threads = False
    block_on_close = True


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

        server = OwnedServer(("127.0.0.1", 0), Peer)
        thread = None
        started = False
        previous = os.environ.get(VARIABLE)
        config = directory / "fixture.json"
        try:
            thread = threading.Thread(target=server.serve_forever, name="owned-admission-fixture")
            config.write_text(json.dumps({"service": "http://127.0.0.1:" + str(server.server_port),
                "identity": corpus["identity"], "admission": admission, "held": str(directory / "held"),
                "invalidAnchors": invalid, "advancedAdmission": advanced, "ownerPid": os.getpid(),
                "unexpectedBootstrap": str(directory / "unexpected-bootstrap")}), encoding="utf-8")
            thread.start()
            started = True
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
            try:
                if started:
                    server.shutdown()
            finally:
                try:
                    server.server_close()
                finally:
                    if started:
                        thread.join(timeout=5)
                        if thread.is_alive():
                            raise AssertionError("Owned admission fixture did not stop")


def serve(output):
    """Serve one native-driver scope. Closing its stdin releases this worker."""
    output = Path(output).absolute()
    pending = output.with_name(output.name + ".pending")
    if output.exists() or pending.exists():
        raise ValueError("Fixture output must be new")
    try:
        with environment():
            data = Path(os.environ[VARIABLE]).read_bytes()
            with pending.open("xb") as stream:
                stream.write(data)
            os.replace(pending, output)
            sys.stdin.buffer.read(1)
    finally:
        pending.unlink(missing_ok=True)
        output.unlink(missing_ok=True)


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--serve":
        raise SystemExit("Pass --serve and a new configuration file path")
    serve(sys.argv[2])
