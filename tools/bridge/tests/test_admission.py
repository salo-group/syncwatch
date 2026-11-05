import unittest
from test_protocol import ContractError
from syncwatch_bridge.engine import Engine


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        owner = type("Owner", (), {"event": lambda _, kind, payload: self.events.append((kind, payload))})()
        self.engine = Engine(owner)
        self.engine.session = object()
        self.transfers = []

    def test_each_transport_gets_an_independent_one_use_challenge(self):
        cancel_old = self.engine.request_admission(self.transfers.append)
        first = self.events[-1][1]["nonce"]
        with self.assertRaises(ContractError):
            self.engine.admit({"nonce": "0" * 32, "ticket": "A" * 64, "clientRunId": "member-run"})
        payload = {"nonce": first, "ticket": "A" * 64, "clientRunId": "member-run"}
        self.engine.admit(payload)
        self.assertEqual(self.transfers, [{"ticket": "A" * 64, "clientRunId": "member-run"}])
        with self.assertRaises(ContractError): self.engine.admit(payload)
        cancel_new = self.engine.request_admission(self.transfers.append)
        second = self.events[-1][1]["nonce"]
        self.assertNotEqual(first, second)
        cancel_old()
        self.assertEqual(self.engine.pending_admission[0], second)
        cancel_new()
        with self.assertRaises(ContractError): self.engine.admit(payload | {"nonce": second})

    def test_parallel_or_closed_admission_never_transfers(self):
        self.engine.request_admission(self.transfers.append)
        with self.assertRaises(ContractError): self.engine.request_admission(self.transfers.append)
        self.engine.session = None
        with self.assertRaises(ContractError):
            self.engine.admit({"nonce": self.events[-1][1]["nonce"], "ticket": "A" * 64, "clientRunId": "member-run"})
        self.assertEqual(self.transfers, [])
