"""One reactor owner serializes validated commands and authoritative observations."""

from collections import OrderedDict
import copy
import json
import time

from syncwatch_ipc.contracts import Context, ContractError
from syncwatch_ipc.stream import Replay


class Controller:
    def __init__(self, registry, send, stop, engine_factory, revision, *, clock=time.monotonic,
                 capacity=64, timeout=5):
        self.registry, self.send, self.stop = registry, send, stop
        self.engine_factory, self.revision = engine_factory, revision
        self.clock, self.capacity, self.timeout = clock, capacity, timeout
        self.context = None
        self.engine = None
        self.closing = False
        self.replay = Replay(pending_limit=capacity)
        self.pending = {}
        self.completed_results = OrderedDict()
        self.sequence = 0
        self.user_sequence = 0
        self.event_sequence = 0
        self.deadline = clock() + timeout
        self.close_deadline = None
        self.stopped = False

    def message(self, kind, payload, **identity):
        return {"v": 1, "type": kind, "clientRunId": self.context.clientRunId,
                "mediaGeneration": self.context.mediaGeneration, "payload": payload, **identity}

    def emit(self, message):
        self.registry.validate("helper-ipc", message)
        data = (json.dumps(message, ensure_ascii=True, separators=(",", ":")) + "\n").encode()
        if len(data) > self.registry.manifest["maxFrameBytes"]:
            raise ContractError("frameTooLarge")
        self.send(data)

    def event(self, kind, payload):
        if self.context is not None and not self.closing:
            self.event_sequence += 1
            self.emit(self.message(kind, payload, id="event-" + str(self.event_sequence)))

    def request(self, kind, payload):
        if self.closing:
            return
        if len(self.pending) >= self.capacity:
            raise ContractError("queueFull")
        self.sequence += 1
        identifier = "cmd-" + str(self.sequence)
        message = self.message(kind, payload, id=identifier, afterUserAction=self.user_sequence)
        self.pending[identifier] = (message, self.clock() + self.timeout)
        self.emit(message)

    def reply(self, command, kind="bridge.result", payload=None):
        response = self.message(kind, {"status": "applied"} if payload is None else payload,
                                replyTo=command["id"])
        self.replay.complete(command, response)
        self.emit(response)

    def receive(self, frame):
        value = self.registry.decode_frame(frame)
        self.registry.validate("helper-ipc", value)
        if self.context is None:
            if value["type"] != "bridge.hello":
                raise ContractError("invalidState")
            self.context = Context(clientRunId=value["clientRunId"], mediaGeneration=value["mediaGeneration"])
        self.registry.accept("helper-ipc", value, self.context)
        if "replyTo" in value:
            pending = self.pending.get(value["replyTo"])
            previous = self.completed_results.get(value["replyTo"])
            if previous is not None:
                if previous != value:
                    raise ContractError("conflict")
                return
            if pending is None or value["type"] not in {"player.result", "bridge.error"}:
                raise ContractError("invalidState")
            del self.pending[value["replyTo"]]
            if value["type"] == "bridge.error":
                if value["payload"]["code"] == "cancelled" and pending[0]["afterUserAction"] < self.user_sequence:
                    self.remember_result(value)
                    return
                raise ContractError("unavailable")
            self.engine.player_result(value["payload"])
            self.remember_result(value)
            return
        kind = value["type"]
        if kind == "player.state":
            if self.engine is None or self.closing:
                raise ContractError("invalidState")
            if value.get("origin") == "user":
                if value["actionSequence"] <= self.user_sequence:
                    raise ContractError("duplicate")
                self.user_sequence = value["actionSequence"]
            self.engine.observe(value["payload"], user_initiated=value.get("origin") == "user")
            return
        fresh, result = self.replay.admit(value)
        if not fresh:
            if result is not None:
                self.emit(result)
            return
        try:
            if self.closing:
                raise ContractError("cancelled")
            payload = value["payload"]
            if kind == "bridge.hello":
                if self.engine is not None:
                    raise ContractError("invalidState")
                self.engine = self.engine_factory(self)
                self.reply(value, "bridge.ready", {"syncplayRevision": self.revision, "admissionVersion": 1})
            elif kind == "bridge.shutdown":
                self.closing = True
                self.close_deadline = self.clock() + self.timeout
                self.engine.close(lambda: self.finished(value))
            elif kind == "sync.connect":
                if "secretHandle" in payload:
                    raise ContractError("unavailable")
                self.engine.connect(payload)
                self.reply(value)
            elif kind == "sync.admit":
                self.engine.admit(payload)
                self.reply(value)
            elif kind == "sync.disconnect":
                self.engine.disconnect(lambda: self.reply(value))
            elif kind == "sync.setReady":
                self.engine.ready(payload["ready"])
                self.reply(value)
            elif kind == "player.loaded":
                self.engine.loaded(payload)
                self.reply(value)
            else:
                raise ContractError("invalidState")
        except ContractError as error:
            self.reply(value, "bridge.error", {"code": error.code, "message": error.code, "retryable": False})

    def remember_result(self, value):
        self.completed_results[value["replyTo"]] = copy.deepcopy(value)
        while len(self.completed_results) > 128:
            self.completed_results.popitem(last=False)

    def finished(self, command=None):
        if self.stopped:
            return
        self.stopped = True
        self.pending.clear()
        if command is not None:
            self.reply(command, "bridge.stopped", {})
        self.stop()

    def abort(self):
        if not self.closing:
            self.closing = True
            self.close_deadline = self.clock() + self.timeout
            if self.engine is not None:
                self.engine.close(self.finished)
            else:
                self.finished()

    def tick(self):
        now = self.clock()
        if self.closing:
            if not self.stopped and now >= self.close_deadline:
                self.finished()
            return
        if self.context is None and now >= self.deadline:
            raise ContractError("timeout")
        if any(now >= deadline for _, deadline in self.pending.values()):
            raise ContractError("timeout")
        if self.engine is not None and not self.closing:
            self.engine.tick()
