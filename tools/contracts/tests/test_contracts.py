import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_contracts import Context, ContractError, Registry, check_fixtures, strict_json


class ContractChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = Registry()
        cls.cases = strict_json((cls.registry.directory / "fixtures/acceptance.json").read_bytes())

    def case(self, name):
        return copy.deepcopy(next(case for case in self.cases if case["name"] == name))

    def reject(self, code, operation):
        with self.assertRaises(ContractError) as caught:
            operation()
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)

    def test_shared_acceptance_corpus(self):
        self.assertEqual(check_fixtures(self.registry), len(self.cases))

    def test_all_message_variants_have_valid_examples(self):
        for kind in ("helper-ipc", "room-event"):
            schema = self.registry.documents[f"https://schemas.syncwatch.invalid/v1/{kind}.schema.json"]
            expected = set(schema["properties"]["type"]["enum"])
            covered = {case["value"]["type"] for case in self.cases if case["schema"] == kind and case["error"] is None}
            self.assertEqual(covered, expected)

    def test_validation_does_not_mutate_input_or_context(self):
        case = self.case("room-media.missing")
        original = copy.deepcopy(case)
        context = Context(**case["context"])
        self.registry.accept(case["schema"], case["value"], context)
        self.assertEqual(case, original)
        self.assertEqual(context, Context(**original["context"]))

    def test_schema_compilation_does_not_fetch_network(self):
        with patch("urllib.request.urlopen", side_effect=AssertionError("Network access is forbidden")):
            Registry()

    def test_unknown_schema_reference_cannot_fetch_network(self):
        for uri in ("https://example.invalid/schema", "file:///private/schema.json", "other:unknown"):
            with self.subTest(uri=uri), self.assertRaisesRegex(ValueError, "offline registry"):
                self.registry.check_references({"$ref": uri})

    def test_exact_byte_limit_includes_terminator(self):
        limit = self.registry.manifest["maxFrameBytes"]
        frame = b"{}" + b" " * (limit - 3) + b"\n"
        self.assertEqual(self.registry.decode_frame(frame), {})
        self.reject("frameTooLarge", lambda: self.registry.decode_frame(b" " + frame))

    def test_unicode_limit_uses_encoded_bytes(self):
        frame = (json.dumps({"text": "\u00e9" * 32768}, ensure_ascii=False) + "\n").encode()
        self.assertLess(len(frame.decode()), self.registry.manifest["maxFrameBytes"])
        self.reject("frameTooLarge", lambda: self.registry.decode_frame(frame))

    def test_malformed_frames(self):
        for frame in (b"\xff\n", b"\xef\xbb\xbf{}\n", b"{}\r\n", b"{}\n{}\n", b"{}", b"\n", b'{"x":1e999}\n'):
            with self.subTest(frame=frame):
                self.reject("malformed", lambda: self.registry.decode_frame(frame))

    def test_duplicate_nested_keys_fail(self):
        self.reject("malformed", lambda: self.registry.decode_frame(b'{"payload":{"x":1,"x":2}}\n'))

    def test_escaped_newline_is_not_a_second_frame(self):
        self.assertEqual(self.registry.decode_frame(b'{"text":"one\\ntwo"}\n'), {"text": "one\ntwo"})

    def test_lone_surrogates_fail(self):
        self.reject("malformed", lambda: self.registry.decode_frame(b'{"text":"\\ud800"}\n'))

    def test_unicode_scalar_pair_passes(self):
        self.assertEqual(self.registry.decode_frame(b'{"text":"\\ud83c\\udfac"}\n'), {"text": "\U0001f3ac"})

    def test_excessive_depth_fails(self):
        frame = b"[" * 33 + b"0" + b"]" * 33 + b"\n"
        self.reject("malformed", lambda: self.registry.decode_frame(frame))

    def test_integer_fields_reject_floats_and_booleans(self):
        case = self.case("ipc-player.setPosition")
        for position in (True, 1.0, 1.5, -1, 604800001):
            with self.subTest(position=position):
                value = {**case["value"], "payload": {"positionMs": position}}
                self.reject("malformed", lambda: self.registry.validate("helper-ipc", value))

    def test_safe_integer_counter_limit(self):
        case = self.case("ipc-player.setPosition")
        for generation, error in ((9007199254740991, None), (9007199254740992, "malformed")):
            value = {**case["value"], "mediaGeneration": generation}
            if error:
                self.reject(error, lambda: self.registry.validate("helper-ipc", value))
            else:
                self.registry.validate("helper-ipc", value)

    def test_rate_can_be_fractional_but_finite_and_bounded(self):
        case = self.case("ipc-player.setSpeed")
        for rate in (0.25, 0.5, 1, 4):
            self.registry.validate("helper-ipc", {**case["value"], "payload": {"rate": rate}})
        for rate in (True, 0, 4.01, float("inf"), float("nan")):
            with self.subTest(rate=rate):
                self.reject("malformed", lambda: self.registry.validate("helper-ipc", {**case["value"], "payload": {"rate": rate}}))

    def test_received_commands_require_trusted_context(self):
        value = self.case("ipc-player.setPosition")["value"]
        self.reject("invalidState", lambda: self.registry.accept("helper-ipc", value, Context()))

    def test_old_process_and_generation_are_independent(self):
        value = self.case("ipc-player.setPosition")["value"]
        for context, error in ((Context(clientRunId="run-8", mediaGeneration=3), "foreignRun"), (Context(clientRunId="run-7", mediaGeneration=4), "staleGeneration")):
            self.reject(error, lambda: self.registry.accept("helper-ipc", value, context))

    def test_room_lifecycle_advances_exactly_one_generation(self):
        case = self.case("room-watch.started")
        context = Context(**case["context"])
        for generation in (3, 4, 6):
            value = {**case["value"], "watchGeneration": generation}
            self.reject("staleGeneration", lambda: self.registry.accept("room-event", value, context))

    def test_non_lifecycle_events_cannot_advance_generation(self):
        case = self.case("room-media.missing")
        value = {**case["value"], "watchGeneration": 5}
        self.reject("staleGeneration", lambda: self.registry.accept("room-event", value, Context(**case["context"])))

    def test_ordered_member_binding_allows_replacement_run(self):
        case = self.case("room-member.bound")
        case["value"]["clientRunId"] = "run-8"
        case["value"]["payload"]["clientRunId"] = "run-8"
        self.registry.accept("room-event", case["value"], Context(**case["context"]))
        case["value"]["payload"]["clientRunId"] = "run-9"
        self.reject("conflict", lambda: self.registry.validate("room-event", case["value"]))

    def test_room_snapshot_rejects_ambiguous_bindings(self):
        value = self.case("room-snapshot")["value"]
        for extra in (value["members"][0], {**value["members"][0], "memberId": "member-2"}):
            candidate = {**value, "members": [value["members"][0], extra]}
            self.reject("conflict", lambda: self.registry.validate("room-snapshot", candidate))

    def test_catalog_batch_is_bounded(self):
        value = self.case("room-catalog.updated")["value"]
        value["payload"]["items"] *= 101
        self.reject("malformed", lambda: self.registry.validate("room-event", value))

    def test_catalog_rejects_repeated_media_identity(self):
        value = self.case("room-catalog.updated")["value"]
        value["payload"]["items"] *= 2
        self.reject("conflict", lambda: self.registry.validate("room-event", value))

    def test_title_key_is_deterministic_and_not_edition_identity(self):
        value = self.case("media-unconfirmed-edition")["value"]
        self.registry.validate("media-descriptor", {**value, "title": "  EXAMPLE   Movie  "})
        self.registry.validate("media-descriptor", {**value, "title": "Cafe\u0301", "titleKey": "caf\u00e9"})
        self.reject("invalidState", lambda: self.registry.validate("media-descriptor", {**value, "titleKey": "different"}))

    def test_open_file_accepts_only_local_opaque_handle(self):
        value = self.case("ipc-player.openFile")["value"]
        for payload in ({"path": "/private/movie.mp4"}, {"mediaHandle": "/private/movie.mp4"}, {"mediaHandle": "https://example.invalid/movie.mp4"}):
            self.reject("malformed", lambda: self.registry.validate("helper-ipc", {**value, "payload": payload}))

    def test_request_and_response_correlation_cannot_be_mixed(self):
        value = self.case("ipc-player.setPosition")["value"]
        self.reject("malformed", lambda: self.registry.validate("helper-ipc", {**value, "replyTo": "cmd-41"}))
        value = self.case("ipc-player.result")["value"]
        self.reject("malformed", lambda: self.registry.validate("helper-ipc", {**value, "id": "cmd-43"}))

    def test_command_sequence_is_positive_and_bounded(self):
        value = self.case("ipc-player.setPosition")["value"]
        for command in ("cmd-0", "cmd-01", "other-1", "cmd-9007199254740992"):
            self.reject("malformed", lambda: self.registry.validate("helper-ipc", {**value, "id": command}))
        self.registry.validate("helper-ipc", {**value, "id": "cmd-9007199254740991"})

    def test_unloaded_player_cannot_be_playing_or_seekable(self):
        value = self.case("playback-unloaded")["value"]
        for changed in ({"paused": False}, {"seekable": True}, {"rate": 2}, {"positionMs": 1}, {"durationMs": 1}):
            self.reject("invalidState", lambda: self.registry.validate("playback-state", {**value, **changed}))

    def test_provider_capabilities_are_explicit_booleans(self):
        value = self.case("call-capabilities-unverified")["value"]
        candidate = {**value, "canObserveJoin": "unverified"}
        self.reject("malformed", lambda: self.registry.validate("call-capabilities", candidate))
        candidate = {key: item for key, item in value.items() if key != "canObserveJoin"}
        self.reject("malformed", lambda: self.registry.validate("call-capabilities", candidate))

    def test_sync_connection_requires_actual_tls_boolean(self):
        value = self.case("ipc-sync.connect")["value"]
        for tls in (False, 0, 1, "true"):
            candidate = {**value, "payload": {**value["payload"], "tls": tls}}
            self.reject("malformed", lambda: self.registry.validate("helper-ipc", candidate))

    def test_meeting_navigation_rejects_non_provider_urls(self):
        value = self.case("room-call.linkChanged")["value"]
        for uri in ("http://meet.google.com/abc-defg-hij", "https://meet.google.com.example.invalid/abc-defg-hij", "javascript:alert(1)"):
            self.reject("malformed", lambda: self.registry.validate("room-event", {**value, "payload": {"meetUri": uri}}))


if __name__ == "__main__":
    unittest.main()
