from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "packages/ipc"), str(ROOT / "packages/syncplay-helper")]
from syncwatch_bridge.engine import Engine, PlayerPort


class EnginePortTests(unittest.TestCase):
    def setUp(self):
        self.events, self.observed, self.requests = [], [], []
        controller = SimpleNamespace(event=lambda k, p: self.events.append((k, p)), pending={})
        self.engine = Engine(controller)
        self.engine.port = PlayerPort(controller)
        self.engine.port.request_state(lambda paused, position, **origin: self.observed.append((paused, position, origin["user_initiated"])))
        self.engine.media = {"durationMs": 60000}
        self.state = {"status": "ready", "durationMs": 60000, "positionMs": 1200,
                      "paused": False, "rate": 1, "seekable": True}
        user = SimpleNamespace(username="player-a", room="room", isReady=lambda: False)
        self.engine.session = SimpleNamespace(_protocol=None, has_initial_state=True,
            getUsername=lambda: user.username,
            userlist=SimpleNamespace(currentUser=user, _users={}),
            changeReadyState=self.requests.append)

    def test_disconnected_observation_is_retained_without_calling_protocol(self):
        self.engine.observe(self.state)
        self.assertEqual(self.observed, [])
        self.assertEqual(self.engine.port.state, self.state)
        self.engine.session._protocol = SimpleNamespace(logged=True)
        self.engine.observe(self.state)
        self.assertEqual(self.observed, [(False, 1200, False)])

    def test_only_explicit_user_origin_can_authorize_upstream_user_detection(self):
        self.engine.session._protocol = SimpleNamespace(logged=True)
        self.engine.observe(self.state, user_initiated=True)
        self.assertEqual(self.observed, [(False, 1200, True)])

    def test_correction_result_cannot_become_a_user_action(self):
        self.engine.port.state = dict(self.state)
        self.engine.session._protocol = SimpleNamespace(logged=True)
        self.engine.player_result({"status": "applied", "positionMs": 0, "paused": True})
        self.assertEqual(self.observed, [(True, 0, False)])
        self.assertEqual(self.engine.port.state["paused"], True)

    def test_readiness_request_does_not_publish_optimistic_state(self):
        self.engine.port.state = dict(self.state)
        self.engine.status = "connected"
        self.engine.ready(True)
        self.assertEqual(self.requests, [True])
        self.assertEqual(self.events, [])
        self.engine.tick()
        self.assertIn(("sync.readiness", {"ready": False}), self.events)
        self.engine.session.userlist.currentUser.isReady = lambda: True
        self.engine.tick()
        self.assertEqual(self.events[-2], ("sync.readiness", {"ready": True}))

    def test_established_transport_loss_retains_owner_and_republishes_identity(self):
        self.engine.tick()
        owner = self.engine.session
        self.engine.session.has_initial_state = False
        self.engine.event("sync.disconnected", {"code": "connection_lost"})
        self.assertEqual(self.engine.status, "connecting")
        self.assertIn(("sync.readiness", {"ready": False}), self.events)
        self.assertEqual(self.events[-1], ("sync.participants", {"participants": []}))
        before = list(self.events)
        self.engine.tick()
        self.assertEqual(self.events, before)
        self.engine.session.has_initial_state = True
        self.engine.tick()
        self.assertIs(self.engine.session, owner)
        self.assertEqual(self.engine.status, "connected")
        self.assertEqual(self.events.count(("sync.identity", {"username": "player-a"})), 2)

    def test_tls_rejection_cannot_be_changed_to_recoverable_by_connection_lost(self):
        self.engine.tick()
        self.engine.event("sync.disconnected", {"code": "tls_required"})
        self.engine.event("sync.disconnected", {"code": "connection_lost"})
        self.engine.tick()
        self.assertEqual(self.engine.status, "disconnected")
        self.assertTrue(self.engine.terminal)

    def test_loss_before_first_authenticated_state_is_terminal(self):
        self.engine.event("sync.disconnected", {"code": "connection_lost"})
        self.engine.tick()
        self.assertEqual(self.engine.status, "disconnected")
        self.assertTrue(self.engine.terminal)

    def test_recovery_cannot_request_optimistic_readiness(self):
        self.engine.tick()
        self.engine.port.state = dict(self.state)
        self.engine.event("sync.disconnected", {"code": "connection_lost"})
        with self.assertRaisesRegex(ValueError, "invalidState"):
            self.engine.ready(True)
        self.assertEqual(self.requests, [])

    def test_pending_correction_is_ordering_state_not_an_observed_value(self):
        self.assertFalse(self.engine.port.state_pending())
        self.engine.port.controller.pending["cmd-1"] = ({"paused": False}, 1)
        self.assertTrue(self.engine.port.state_pending())
        self.assertIsNone(self.engine.port.state)

    def test_identity_waits_for_authenticated_initial_state_and_uses_accepted_name(self):
        self.engine.session.has_initial_state = False
        self.engine.tick()
        self.assertFalse(any(kind == "sync.identity" for kind, _ in self.events))
        self.engine.session.userlist.currentUser.username = "player-a_2"
        self.engine.session.has_initial_state = True
        self.engine.tick()
        identity = ("sync.identity", {"username": "player-a_2"})
        self.assertEqual(self.events.count(identity), 1)
        self.assertLess(self.events.index(identity), self.events.index(("sync.connection", {"state": "connected"})))
        self.engine.tick()
        self.assertEqual(self.events.count(identity), 1)

    def test_application_play_uses_the_upstream_direct_unpause_policy(self):
        settings = {}
        connected = []
        class Session:
            def __init__(self, *args, **kwargs):
                pass

            def getConfig(self):
                return settings

            def connect(self):
                connected.append(dict(settings))
        engine = Engine(SimpleNamespace(event=lambda *args: None, pending={}))
        with patch.dict(sys.modules, {
                "syncwatch_syncplay.session": SimpleNamespace(Session=Session),
                "syncplay": SimpleNamespace(constants=SimpleNamespace(UNPAUSE_ALWAYS_MODE="Always"))}):
            engine.connect({"host": "localhost", "port": 8999, "username": "viewer", "room": "room"})
        self.assertEqual(connected, [{"sharedPlaylistEnabled": False, "unpauseAction": "Always"}])

    def test_released_native_port_cannot_publish_again(self):
        self.engine.port.release()
        self.engine.session._protocol = SimpleNamespace(logged=True)
        self.engine.observe(self.state)
        self.assertEqual(self.observed, [])


if __name__ == "__main__":
    unittest.main()
