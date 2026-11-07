"""The owned peer releases sockets and environment state after a failed native check."""

import json
import os
from pathlib import Path
import socket
import sys
import time
import unittest
from urllib.parse import urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools/consumers"))
import admission_fixture


class AdmissionFixtureTests(unittest.TestCase):
    def test_failed_check_closes_peer_and_restores_environment(self):
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
        with socket.socket() as connection:
            self.assertNotEqual(connection.connect_ex((address.hostname, address.port)), 0)


if __name__ == "__main__":
    unittest.main()
