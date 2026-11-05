"""Account admission boundary around the pinned Syncplay server.

The injected authority belongs to the service host, not the TCP client. It must
consume the ticket exactly once, return a live lease, and fail closed when its
private control channel is lost. No stock listener is created by this module.
"""

from dataclasses import dataclass
import json
import math
import re
import time

from twisted.internet import defer, reactor
from twisted.internet.interfaces import IHandshakeListener
from zope.interface import implementer
from syncplay.protocols import SyncServerProtocol
from syncplay.server import SyncFactory


@dataclass(frozen=True, repr=False)
class AdmissionLease:
    lease_id: str
    member_id: str
    room_epoch: str
    client_run_id: str
    username: str
    room: str
    deadline: float

    def __repr__(self):
        return "AdmissionLease([redacted])"


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def _invalid_constant(_):
    raise ValueError("non-finite number")


def _text(value, maximum):
    return isinstance(value, str) and 0 < len(value) <= maximum and all(32 <= ord(c) < 127 for c in value)


def _bounded(value, depth=0):
    if depth > 16:
        raise ValueError("message depth")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite number")
    if isinstance(value, dict):
        for part in value.values():
            _bounded(part, depth + 1)
    elif isinstance(value, list):
        for part in value:
            _bounded(part, depth + 1)


def _number(value):
    return type(value) in (int, float) and abs(value) <= 9007199254740991 and math.isfinite(value)


def _valid_lease(lease):
    return (isinstance(lease, AdmissionLease) and _text(lease.lease_id, 64) and _text(lease.member_id, 64) and _text(lease.room_epoch, 64)
            and _text(lease.client_run_id, 64) and _text(lease.username, 16) and _text(lease.room, 35)
            and type(lease.deadline) in (int, float) and abs(lease.deadline) <= 9007199254740991 and math.isfinite(lease.deadline)
            and 0 < lease.deadline - time.monotonic() <= 10)


def _features(value):
    return (isinstance(value, dict) and len(value) <= 64
            and all(_text(key, 64) and (type(part) is bool or _number(part) or _text(part, 64)) for key, part in value.items()))


@implementer(IHandshakeListener)
class AdmissionProtocol(SyncServerProtocol):
    MAX_LENGTH = 16384

    def __init__(self, factory):
        super().__init__(factory)
        self._phase = "tls"
        self._lease = None
        self._pending = None
        self._deadline = None
        self._audit = None

    def connectionMade(self):
        self._factory.connections.add(self)
        self._deadline = reactor.callLater(10, self._reject)

    def dropWithError(self, _):
        # Upstream error strings may include arbitrary client fields. Neither
        # those fields nor admission tickets are sent to diagnostics.
        self._reject()

    def lineLengthExceeded(self, _):
        self._reject()

    def _reject(self):
        if self._phase == "closed":
            return
        self._phase = "closed"
        self._cancel(self._deadline)
        self._cancel(self._audit)
        pending, self._pending = self._pending, None
        if pending is not None and not pending.called:
            pending.cancel()
        self.transport.abortConnection()

    @staticmethod
    def _cancel(call):
        if call is not None and call.active():
            call.cancel()

    def lineReceived(self, line):
        if self._phase == "closed":
            return
        try:
            message = json.loads(line.decode("utf-8"), object_pairs_hook=_object, parse_constant=_invalid_constant)
            _bounded(message)
            self.handleMessages(message)
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
            self._reject()

    def handleMessages(self, message):
        # Validate a whole frame before the upstream dispatcher can mutate a
        # watcher. Its requireLogged decorator alone does not stop execution.
        if not isinstance(message, dict) or len(message) != 1:
            self._reject()
            return
        command, value = next(iter(message.items()))
        if self._phase == "tls":
            if command != "TLS" or value != {"startTLS": "send"}:
                self._reject()
                return
            self._phase = "handshaking"
            super().handleTLS(value)
        elif self._phase == "admission":
            if command != "Admission":
                self._reject()
                return
            self._admit(value)
        elif self._phase == "hello":
            if command != "Hello" or not self._valid_hello(value) or not self._live():
                self._reject()
                return
            self._phase = "logged"
            super().handleHello(value)
            if not self._logged or not self._identity_matches():
                self._reject()
        elif self._phase == "logged":
            if not self._live() or not self._valid_command(command, value):
                self._reject()
                return
            super().handleMessages(message)
        else:
            self._reject()

    def handshakeCompleted(self):
        if self._phase != "handshaking":
            self._reject()
            return
        self._phase = "admission"

    def _admit(self, value):
        if (not isinstance(value, dict) or set(value) != {"ticket", "clientRunId", "username", "room"}
                or not isinstance(value["ticket"], str) or re.fullmatch(r"[0-9A-F]{64}", value["ticket"]) is None
                or not _text(value["clientRunId"], 64) or not _text(value["username"], 16) or not _text(value["room"], 35)):
            self._reject()
            return
        self._phase = "validating"
        expected = (value["clientRunId"], value["username"], value["room"])
        pending = defer.maybeDeferred(self._factory.authority.admit, value["ticket"], *expected)
        self._pending = pending
        def accepted(lease):
            self._pending = None
            if self._phase != "validating":
                return
            if not _valid_lease(lease) or (lease.client_run_id, lease.username, lease.room) != expected:
                self._reject()
                return
            self._lease = lease
            if not self._live() or not self._factory.claim(self):
                self._reject()
                return
            self._phase = "hello"
            self._cancel(self._deadline)
            self._deadline = reactor.callLater(10, self._reject)
            self.sendMessage({"Admission": {"accepted": True, "username": lease.username, "room": lease.room}})
            self._audit = reactor.callLater(0.25, self._check_lease)
        def failed(_):
            self._pending = None
            self._reject()
            return None
        pending.addCallback(accepted)
        pending.addErrback(failed)

    def _live(self):
        try:
            return self._lease is not None and self._lease.deadline > time.monotonic() and self._factory.authority.is_active(self._lease) is True
        except Exception:
            return False

    def _check_lease(self):
        if self._phase == "closed":
            return
        if not self._live():
            self._reject()
        else:
            self._audit = reactor.callLater(0.25, self._check_lease)

    def _valid_hello(self, hello):
        return (isinstance(hello, dict) and set(hello) <= {"username", "room", "version", "realversion", "features"}
                and hello.get("username") == self._lease.username and hello.get("room") == {"name": self._lease.room}
                and _text(hello.get("version"), 32) and ("realversion" not in hello or _text(hello["realversion"], 32))
                and _features(hello.get("features", {})))

    def _identity_matches(self):
        return (self._watcher is not None and self._watcher.getName() == self._lease.username
                and self._watcher.getRoom() is not None and self._watcher.getRoom().getName() == self._lease.room)

    def sendHello(self, version):
        # Upstream can rename collisions and truncate identities. Check before
        # publishing its accepted Hello, not after the client starts playing.
        if not self._identity_matches():
            self._reject()
            return
        self._cancel(self._deadline)
        super().sendHello(version)

    def _valid_command(self, command, value):
        if command == "List":
            return value == {}
        if command == "State":
            if not isinstance(value, dict) or not set(value) <= {"playstate", "ping", "ignoringOnTheFly"} or not all(isinstance(part, dict) for part in value.values()):
                return False
            if "playstate" in value:
                state = value["playstate"]
                if (not set(state) <= {"position", "paused", "doSeek"}
                        or "position" in state and not _number(state["position"])
                        or any(key in state and type(state[key]) is not bool for key in ("paused", "doSeek"))):
                    return False
            for key, fields in (("ping", {"latencyCalculation", "clientLatencyCalculation", "clientRtt"}),
                                ("ignoringOnTheFly", {"server", "client"})):
                if key in value and (not set(value[key]) <= fields or not all(_number(part) for part in value[key].values())):
                    return False
            return True
        if command != "Set" or not isinstance(value, dict) or not value or not set(value) <= {"file", "ready", "features"}:
            return False
        if not all(isinstance(part, dict) for part in value.values()):
            return False
        if "features" in value and not _features(value["features"]):
            return False
        if "file" in value and value["file"]:
            file = value["file"]
            name = file.get("name")
            if (set(file) != {"name", "duration", "size"} or not isinstance(name, str) or not 0 < len(name) <= 1024
                    or any(ord(character) < 32 or character in "/\\\x7f" for character in name)
                    or not _number(file["duration"]) or file["duration"] < 0
                    or not (_number(file["size"]) and file["size"] >= 0 or _text(file["size"], 64))):
                return False
        if "ready" in value:
            ready = value["ready"]
            if (not set(ready) <= {"isReady", "manuallyInitiated", "username"} or "isReady" not in ready or not (ready["isReady"] is None or type(ready["isReady"]) is bool)
                    or "manuallyInitiated" in ready and type(ready["manuallyInitiated"]) is not bool
                    or "username" in ready and ready["username"] != self._lease.username):
                return False
        return True

    def connectionLost(self, reason):
        self._reject()
        self._factory.release(self)
        self._factory.connections.discard(self)
        super().connectionLost(reason)


class AdmissionFactory(SyncFactory):
    def __init__(self, authority, *, tls_directory, salt):
        self.authority = authority
        self.connections = set()
        self.members = {}
        super().__init__(port="0", salt=salt, isolateRooms=True, disableChat=True, tlsCertPath=str(tls_directory))
        if not self.serverAcceptsTLS or self.options is None:
            raise ValueError("The admission listener requires its owned TLS certificate")

    def buildProtocol(self, address):
        return AdmissionProtocol(self)

    def claim(self, connection):
        key = (connection._lease.room_epoch, connection._lease.member_id)
        if key in self.members:
            return False
        if any(other._lease.username == connection._lease.username for other in self.members.values()):
            return False
        # Keep the first live transport until it is actually reaped. A replay
        # cannot replace another connection or delete its watcher on loss.
        self.members[key] = connection
        return True

    def renew(self, lease):
        if not _valid_lease(lease):
            raise ValueError("Invalid admission lease renewal")
        connection = self.members.get((lease.room_epoch, lease.member_id))
        if connection is None:
            return False
        previous = connection._lease
        if previous.lease_id != lease.lease_id:
            return False
        if (previous.client_run_id, previous.username, previous.room) != (lease.client_run_id, lease.username, lease.room):
            connection._reject()
            return False
        if not connection._live():
            connection._reject()
            return False
        connection._lease = lease
        return True

    def revoke(self, room_epoch, member_id):
        connection = self.members.get((room_epoch, member_id))
        if connection is not None:
            connection._reject()

    def release(self, connection):
        lease = connection._lease
        if lease is not None:
            key = (lease.room_epoch, lease.member_id)
            if self.members.get(key) is connection:
                del self.members[key]

    def close(self):
        for connection in tuple(self.connections):
            connection._reject()
