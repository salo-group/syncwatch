import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "tools/contracts"), str(ROOT / "packages/syncplay-helper")]
from check_contracts import ContractError, Registry
from syncwatch_ipc.stream import Frames, Replay
from syncwatch_bridge.controller import Controller


class Engine:
    def __init__(self, controller):
        self.controller = controller
        self.actions = []

    def connect(self, payload):
        self.actions.append(("connect", payload))

    def admit(self, payload):
        self.actions.append(("admit", payload))

    def observe(self, payload, *, user_initiated=False):
        self.actions.append(("observe", payload, user_initiated))

    def player_result(self, payload):
        self.actions.append(("result", payload))

    def close(self, done):
        self.actions.append(("close", None))
        done()

    def tick(self):
        pass


class ProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = Registry()

    def setUp(self):
        self.sent, self.stopped = [], []
        self.now = 0
        self.controller = Controller(self.registry, self.sent.append, lambda: self.stopped.append(True),
                                     Engine, "a" * 40, clock=lambda: self.now)

    def command(self, kind="bridge.hello", number=1, **changes):
        payload = {"supportedVersions": [1]} if kind == "bridge.hello" else {}
        return {"v": 1, "id": "cmd-" + str(number), "type": kind, "clientRunId": "run-1",
                "mediaGeneration": 2, "payload": payload, **changes}

    def receive(self, value):
        self.controller.receive((json.dumps(value) + "\n").encode())

    def rejection(self, code, value):
        with self.assertRaises(ContractError) as caught:
            self.receive(value)
        self.assertEqual(caught.exception.code, code)

    def test_admission_capability_and_scoped_transfer(self):
        self.receive(self.command())
        self.assertEqual(json.loads(self.sent[-1])["payload"]["admissionVersion"], 1)
        payload = {"nonce": "a" * 32, "ticket": "A" * 64, "clientRunId": "room-run"}
        command = self.command("sync.admit", 2, payload=payload)
        self.receive(command)
        self.receive(command)
        self.assertEqual(self.controller.engine.actions, [("admit", payload)])
        self.rejection("foreignRun", self.command("sync.admit", 3, payload=payload, clientRunId="obsolete"))
        self.rejection("malformed", self.command("sync.admit", 3, payload=payload | {"accessToken": "forbidden"}))

    def test_partial_and_coalesced_frames(self):
        frames = Frames(8)
        self.assertEqual(list(frames.feed(b'{"x"')), [])
        self.assertEqual(list(frames.feed(b':1}\n{}\n')), [b'{"x":1}\n', b'{}\n'])
        frames.eof()

    def test_unterminated_oversized_and_partial_eof(self):
        with self.assertRaisesRegex(ContractError, "frameTooLarge"):
            list(Frames(8).feed(b"x" * 9))
        frames = Frames()
        list(frames.feed(b"{"))
        with self.assertRaisesRegex(ContractError, "malformed"):
            frames.eof()

    def test_hello_precedes_all_work(self):
        self.rejection("invalidState", self.command("sync.disconnect"))
        self.receive(self.command())
        self.assertEqual(json.loads(self.sent[-1])["type"], "bridge.ready")

    def test_replay_never_reallocates_engine_and_conflict_is_rejected(self):
        hello = self.command()
        self.receive(hello)
        engine = self.controller.engine
        self.receive(hello)
        self.assertIs(self.controller.engine, engine)
        self.assertEqual(self.sent[0], self.sent[1])
        self.rejection("conflict", self.command("sync.disconnect"))

    def test_stale_and_foreign_input_have_no_effect(self):
        self.receive(self.command())
        self.rejection("foreignRun", self.command("sync.disconnect", 2, clientRunId="other"))
        self.rejection("staleGeneration", self.command("sync.disconnect", 2, mediaGeneration=1))
        self.assertEqual(self.controller.engine.actions, [])

    def test_direction_is_enforced(self):
        self.receive(self.command())
        self.receive(self.command("player.setPaused", 2, payload={"paused": False}))
        self.assertEqual(json.loads(self.sent[-1])["payload"]["code"], "invalidState")
        self.assertEqual(self.controller.engine.actions, [])

    def test_correction_ack_is_not_dispatched_as_a_user_state_event(self):
        self.receive(self.command())
        self.controller.request("player.setPaused", {"paused": False})
        self.assertEqual(self.controller.engine.actions, [])
        reply = self.command("player.result", payload={"status": "applied", "positionMs": 0, "paused": False})
        del reply["id"]
        reply["replyTo"] = "cmd-1"
        self.receive(reply)
        self.assertEqual(self.controller.engine.actions[0][0], "result")
        self.receive(reply)
        self.assertEqual(len(self.controller.engine.actions), 1)
        changed = copy.deepcopy(reply)
        changed["payload"]["positionMs"] = 1
        self.rejection("conflict", changed)

    def test_completed_reverse_results_are_bounded_and_old_replay_cannot_execute(self):
        self.receive(self.command())
        first = None
        for number in range(1, 131):
            self.controller.request("player.setPaused", {"paused": True})
            reply = self.command("player.result", payload={"status": "applied", "positionMs": 0, "paused": True})
            del reply["id"]
            reply["replyTo"] = "cmd-" + str(number)
            if first is None:
                first = copy.deepcopy(reply)
            self.receive(reply)
        self.assertEqual(len(self.controller.completed_results), 128)
        self.rejection("invalidState", first)
        self.assertEqual(len(self.controller.engine.actions), 130)

    def test_stale_correction_cancellation_is_correlated_with_newer_user_action(self):
        self.receive(self.command())
        self.controller.request("player.setPaused", {"paused": True})
        request = json.loads(self.sent[-1])
        self.assertEqual(request["afterUserAction"], 0)
        state = self.command("player.state", payload={"status": "ready", "positionMs": 1000,
            "durationMs": 60000, "paused": False, "rate": 1, "seekable": True}, origin="user", actionSequence=1)
        self.receive(state)
        reply = self.command("bridge.error", payload={"code": "cancelled", "message": "cancelled", "retryable": False})
        del reply["id"]
        reply["replyTo"] = "cmd-1"
        self.receive(reply)
        self.assertEqual(self.controller.pending, {})
        self.rejection("duplicate", state)
        self.controller.request("player.setPaused", {"paused": False})
        self.assertEqual(json.loads(self.sent[-1])["afterUserAction"], 1)
        reply["replyTo"] = "cmd-2"
        self.rejection("unavailable", reply)

    def test_timeout_and_bounded_engine_queue(self):
        self.receive(self.command())
        self.controller.capacity = 1
        self.controller.request("player.setPaused", {"paused": True})
        with self.assertRaisesRegex(ContractError, "queueFull"):
            self.controller.request("player.setPaused", {"paused": False})
        self.now = 6
        with self.assertRaisesRegex(ContractError, "timeout"):
            self.controller.tick()

    def test_helper_compiles_only_its_authority_with_the_same_contract_rules(self):
        from syncwatch_ipc.contracts import Registry as SharedRegistry
        import fastjsonschema
        narrow = SharedRegistry(ROOT / "contracts", fastjsonschema, kinds=("helper-ipc",))
        self.assertEqual(set(narrow.validators), {"helper-ipc"})
        narrow.validate("helper-ipc", self.command())
        with self.assertRaises(ContractError):
            narrow.validate("helper-ipc", self.command(origin="user"))
        with self.assertRaises(ValueError):
            SharedRegistry(ROOT / "contracts", fastjsonschema, kinds=("unknown",))

    def test_missing_hello_times_out(self):
        self.now = 6
        with self.assertRaisesRegex(ContractError, "timeout"):
            self.controller.tick()

    def test_eof_closes_engine_once_and_shutdown_is_correlated(self):
        self.receive(self.command())
        self.receive(self.command("bridge.shutdown", 2))
        self.controller.abort()
        self.assertEqual(self.controller.engine.actions, [("close", None)])
        self.assertEqual(self.stopped, [True])
        self.assertEqual(json.loads(self.sent[-1])["replyTo"], "cmd-2")
        self.assertEqual(json.loads(self.sent[-1])["type"], "bridge.stopped")

    def test_completed_eviction_retains_high_water_and_pending_is_not_evicted(self):
        replay = Replay(capacity=1, pending_limit=1)
        first = self.command()
        self.assertEqual(replay.admit(first), (True, None))
        with self.assertRaisesRegex(ContractError, "queueFull"):
            replay.admit(self.command(number=2))
        response = {"accepted": True}
        replay.complete(first, response)
        response["accepted"] = False
        self.assertEqual(replay.admit(first), (False, {"accepted": True}))
        second = self.command(number=2)
        replay.admit(second)
        replay.complete(second, {})
        with self.assertRaisesRegex(ContractError, "duplicate"):
            replay.admit(first)

    def test_replay_freezes_original_payload(self):
        replay = Replay()
        command = self.command()
        original = copy.deepcopy(command)
        replay.admit(command)
        command["payload"]["supportedVersions"].clear()
        self.assertEqual(replay.admit(original), (False, None))


if __name__ == "__main__":
    unittest.main()
