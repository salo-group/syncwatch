"""The owned peer releases sockets and environment state after a failed native check."""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools/consumers"))
import admission_fixture


class AdmissionFixtureTests(unittest.TestCase):
    def assert_closed(self, address):
        with socket.socket() as connection:
            self.assertNotEqual(connection.connect_ex(address), 0)

    def test_setup_failures_close_listener_without_starting_shutdown(self):
        for failure in ("write", "start"):
            with self.subTest(failure=failure):
                servers = []

                class ObservedServer(admission_fixture.OwnedServer):
                    def __init__(self, *arguments):
                        super().__init__(*arguments)
                        servers.append(self)

                target = "pathlib.Path.write_text" if failure == "write" else "threading.Thread.start"
                previous = os.environ.get(admission_fixture.VARIABLE)
                with patch.object(admission_fixture, "OwnedServer", ObservedServer):
                    with patch(target, side_effect=RuntimeError("fixture setup failed")):
                        with self.assertRaisesRegex(RuntimeError, "fixture setup failed"):
                            with admission_fixture.environment(ROOT):
                                self.fail("Failed setup must not run native checks")
                self.assertEqual(os.environ.get(admission_fixture.VARIABLE), previous)
                self.assertEqual(len(servers), 1)
                self.assert_closed(servers[0].server_address)

    def test_worker_stops_on_driver_eof_with_request_in_flight(self):
        with tempfile.TemporaryDirectory(prefix="sw-admission-worker-") as directory:
            output = Path(directory) / "worker.json"
            worker = subprocess.Popen([sys.executable, str(ROOT / "tools/consumers/admission_fixture.py"),
                                       "--serve", str(output)], stdin=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 5
                while not output.exists():
                    self.assertIsNone(worker.poll(), "Fixture exited before publishing its configuration")
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.01)
                fixture = json.loads(output.read_text(encoding="utf-8"))
                self.assertEqual(fixture["ownerPid"], worker.pid)
                address = urlsplit(fixture["service"])
                with socket.create_connection((address.hostname, address.port), timeout=2) as held:
                    held.sendall(b"GET /v1/rooms/room-example/events HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
                    while not Path(fixture["held"]).exists():
                        self.assertLess(time.monotonic(), deadline)
                        time.sleep(0.01)
                    worker.stdin.close()
                    self.assertEqual(worker.wait(timeout=10), 0)
                    self.assertEqual(held.recv(1), b"")
                self.assertFalse(output.exists())
                self.assertFalse(output.with_name(output.name + ".pending").exists())
                self.assertFalse(Path(fixture["held"]).parent.exists())
                self.assert_closed((address.hostname, address.port))
            finally:
                worker.stdin.close()
                if worker.poll() is None:
                    worker.kill()
                    worker.wait(timeout=5)

    def test_failed_check_closes_peer_and_restores_environment(self):
        threads = set(threading.enumerate())
        previous = os.environ.get(admission_fixture.VARIABLE)
        address = None
        with self.assertRaisesRegex(RuntimeError, "owned check failed"):
            with admission_fixture.environment(ROOT):
                config = Path(os.environ[admission_fixture.VARIABLE])
                fixture = json.loads(config.read_text(encoding="utf-8"))
                address = urlsplit(fixture["service"])
                with urlopen(fixture["service"] + "/v1/rooms/room-example/selection", timeout=2) as response:
                    selection = json.load(response)
                self.assertEqual(selection["selection"]["media"]["v"], 1)
                with socket.create_connection((address.hostname, address.port), timeout=2) as held:
                    held.sendall(b"GET /v1/rooms/room-example/events HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
                    deadline = time.monotonic() + 2
                    while not Path(fixture["held"]).exists():
                        self.assertLess(time.monotonic(), deadline)
                        time.sleep(0.01)
                    raise RuntimeError("owned check failed")
        self.assertEqual(os.environ.get(admission_fixture.VARIABLE), previous)
        self.assertFalse(config.exists())
        self.assert_closed((address.hostname, address.port))
        self.assertEqual(set(threading.enumerate()), threads)


if __name__ == "__main__":
    unittest.main()
