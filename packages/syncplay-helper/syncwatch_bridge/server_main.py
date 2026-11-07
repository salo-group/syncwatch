"""Owned admission server. Authority is available only through inherited pipes."""

from collections import deque
import ipaddress
import json
import os
from pathlib import Path
import queue
import secrets
import sys
import threading
import time

from syncwatch_ipc.stream import Frames
from syncwatch_syncplay.reactor import install
from .main import QuietOutput


class ControlAuthority:
    def __init__(self, host):
        self.host = host
        self.pending = {}
        self.leases = {}
        self.retired = deque(maxlen=512)

    def admit(self, ticket, run, username, room):
        from twisted.internet import defer
        if self.host.closing or len(self.pending) >= 128:
            raise ValueError("admission unavailable")
        identifier = secrets.token_hex(16)
        result = defer.Deferred()
        self.pending[identifier] = (result, time.monotonic() + 5)
        try:
            self.host.send({"v": 1, "type": "admission.consume", "id": identifier,
                            "ticket": ticket, "clientRunId": run, "username": username, "room": room})
        except Exception:
            del self.pending[identifier]
            raise ValueError("admission unavailable") from None
        return result

    def is_active(self, lease):
        return (not self.host.closing and self.leases.get(lease.lease_id) == lease
                and lease.deadline > time.monotonic())

    def release(self, lease):
        self.leases.pop(lease.lease_id, None)
        self.retired.append(lease.lease_id)
        if not self.host.closing:
            try:
                self.host.send({"v": 1, "type": "admission.closed", "leaseId": lease.lease_id})
            except Exception:
                self.host.close(failed=True)

    @staticmethod
    def read_lease(value):
        from syncwatch_syncplay.server import AdmissionLease, _valid_lease
        if (not isinstance(value, dict) or set(value) != {"id", "memberId", "roomEpoch", "clientRunId", "username", "room", "expiresIn"}
                or type(value["expiresIn"]) is not int or not 1 <= value["expiresIn"] <= 5):
            raise ValueError("invalid authority lease")
        lease = AdmissionLease(value["id"], value["memberId"], value["roomEpoch"], value["clientRunId"],
                               value["username"], value["room"], time.monotonic() + value["expiresIn"])
        if not _valid_lease(lease):
            raise ValueError("invalid authority lease")
        return lease

    def reply(self, value):
        if not set(value) <= {"v", "type", "replyTo", "status", "lease"} or not {"replyTo", "status"} <= set(value):
            raise ValueError("invalid authority reply")
        result, _ = self.pending.pop(value["replyTo"])
        if value["status"] == "accepted" and "lease" in value:
            lease = self.read_lease(value["lease"])
            if lease.lease_id in self.leases or lease.lease_id in self.retired:
                raise ValueError("duplicate authority lease")
            if result.called:
                self.release(lease)
            else:
                self.leases[lease.lease_id] = lease
                result.callback(lease)
        elif value["status"] == "denied" and "lease" not in value:
            if not result.called:
                result.errback(ValueError("admission rejected"))
        else:
            raise ValueError("invalid authority reply")

    def update(self, value):
        if set(value) != {"v", "type", "lease"}:
            raise ValueError("invalid authority renewal")
        lease = self.read_lease(value["lease"])
        if lease.lease_id in self.retired:
            return
        previous = self.leases[lease.lease_id]
        if (previous.member_id, previous.room_epoch, previous.client_run_id, previous.username, previous.room) != (
                lease.member_id, lease.room_epoch, lease.client_run_id, lease.username, lease.room):
            raise ValueError("changed authority binding")
        # The protocol verifies the old lease before replacing it. Keep the
        # authority mirror unchanged until that check has succeeded.
        if self.host.factory.renew(lease):
            self.leases[lease.lease_id] = lease
        else:
            self.release(previous)

    def revoke(self, value):
        if set(value) != {"v", "type", "leaseId"}:
            raise ValueError("invalid authority revocation")
        identifier = value["leaseId"]
        if identifier in self.retired:
            return
        lease = self.leases.pop(identifier)
        self.retired.append(identifier)
        self.host.factory.revoke(lease.room_epoch, lease.member_id)


class ServerHost:
    def __init__(self, reactor, send):
        self.reactor, self.send = reactor, send
        self.factory = self.listener = None
        self.closing = self.failed = False
        self.deadline = time.monotonic() + 5
        self.authority = ControlAuthority(self)

    def receive(self, frame):
        from syncwatch_syncplay.server import AdmissionFactory, _bounded, _invalid_constant, _object, _text
        value = json.loads(frame.decode("utf-8"), object_pairs_hook=_object, parse_constant=_invalid_constant)
        _bounded(value)
        if not isinstance(value, dict) or type(value.get("v")) is not int or value["v"] != 1 or self.closing:
            raise ValueError("invalid authority frame")
        kind = value.get("type")
        if self.factory is None:
            if (set(value) != {"v", "type", "bindAddress", "port", "tlsDirectory", "salt"} or kind != "server.start"
                    or type(value["port"]) is not int or not 0 <= value["port"] <= 65535
                    or not isinstance(value["tlsDirectory"], str) or not 0 < len(value["tlsDirectory"]) <= 4096
                    or not Path(value["tlsDirectory"]).is_absolute() or not _text(value["salt"], 128) or len(value["salt"]) < 32):
                raise ValueError("invalid server configuration")
            address = str(ipaddress.ip_address(value["bindAddress"]))
            self.factory = AdmissionFactory(self.authority, tls_directory=value["tlsDirectory"], salt=value["salt"])
            self.listener = self.reactor.listenTCP(value["port"], self.factory, interface=address)
            self.send({"v": 1, "type": "server.ready", "port": self.listener.getHost().port})
        elif kind == "admission.result":
            self.authority.reply(value)
        elif kind == "lease.renew":
            self.authority.update(value)
        elif kind == "lease.revoke":
            self.authority.revoke(value)
        elif kind == "server.shutdown" and set(value) == {"v", "type"}:
            self.close()
        else:
            raise ValueError("invalid authority operation")

    def tick(self):
        now = time.monotonic()
        if (self.factory is None and now >= self.deadline
                or any(until <= now for _, until in self.authority.pending.values())):
            self.close(failed=True)

    def close(self, failed=False):
        if self.closing:
            self.failed = self.failed or failed
            return
        self.closing = True
        self.failed = failed
        for result, _ in self.authority.pending.values():
            if not result.called:
                result.cancel()
        self.authority.pending.clear()
        self.authority.leases.clear()
        if self.factory is not None:
            self.factory.close()
        if self.listener is not None:
            self.listener.stopListening()
        self.send({"v": 1, "type": "server.stopped"})


def main():
    sys.stdout = sys.stderr = QuietOutput()
    reactor = install()
    from twisted.internet import task
    outgoing, incoming = queue.Queue(maxsize=512), queue.Queue(maxsize=64)
    terminal, writer_finished = threading.Event(), threading.Event()
    frames = Frames(16384)

    def send(value):
        data = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("ascii") + b"\n"
        if len(data) > 16384:
            raise ValueError("authority frame too large")
        outgoing.put_nowait(data)

    host = ServerHost(reactor, send)
    stop_at = [None]

    def pump():
        try:
            while not host.closing:
                try:
                    data = incoming.get_nowait()
                except queue.Empty:
                    break
                if data is None:
                    frames.eof()
                    host.close(failed=True)
                    break
                for frame in frames.feed(data):
                    host.receive(frame)
            host.tick()
        except Exception:
            try:
                host.close(failed=True)
            except Exception:
                host.failed = host.closing = True
        if host.closing:
            terminal.set()
            if stop_at[0] is None:
                stop_at[0] = time.monotonic() + 2
            connections = host.factory.connections if host.factory is not None else set()
            if writer_finished.is_set() and not connections or time.monotonic() >= stop_at[0]:
                reactor.stop()

    def read():
        try:
            while not terminal.is_set():
                data = os.read(0, 4096)
                incoming.put(data or None, timeout=1)
                reactor.callFromThread(pump)
                if not data:
                    return
        except Exception:
            reactor.callFromThread(host.close, failed=True)

    def write():
        try:
            while not terminal.is_set() or not outgoing.empty():
                try:
                    data = outgoing.get(timeout=0.05)
                except queue.Empty:
                    continue
                while data:
                    count = os.write(1, data)
                    if count <= 0:
                        raise OSError("closed authority pipe")
                    data = data[count:]
        except Exception:
            reactor.callFromThread(host.close, failed=True)
        finally:
            writer_finished.set()

    reader = threading.Thread(target=read, name="admission-input", daemon=True)
    writer = threading.Thread(target=write, name="admission-output", daemon=True)
    reader.start(); writer.start()
    timer = task.LoopingCall(pump); timer.start(0.05)
    reactor.run(installSignalHandlers=False)
    terminal.set()
    if timer.running:
        timer.stop()
    writer.join(timeout=2)
    reader.join(timeout=1)
    if writer.is_alive() or reader.is_alive() or host.failed or host.factory is not None and host.factory.connections:
        raise RuntimeError("Admission control stopped")
