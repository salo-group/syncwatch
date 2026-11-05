"""Real TLS/protocol checks; synthetic authority, no native player or Google acceptance."""

import secrets
from twisted.protocols.basic import LineReceiver
from twisted.internet.interfaces import IHandshakeListener
from zope.interface import implementer
from syncwatch_syncplay.server import AdmissionFactory, AdmissionLease
from syncwatch_syncplay.transport import tls_options


@implementer(IHandshakeListener)
class AdmissionProbe(LineReceiver):
    def __init__(self, roots, admission=None, cleartext=None):
        self.roots, self.admission, self.cleartext = roots, admission, cleartext
        self.messages = []
        self.lost = False
        self.secured = False
        self.logged = False

    def send(self, value):
        self.sendLine(json.dumps(value).encode("utf-8"))

    def connectionMade(self):
        self.send(self.cleartext if self.cleartext is not None else {"TLS": {"startTLS": "send"}})

    def handshakeCompleted(self):
        self.secured = True
        self.send({"Admission": self.admission})

    def lineReceived(self, line):
        message = json.loads(line)
        self.messages.append(message)
        if "TLS" in message:
            assert message == {"TLS": {"startTLS": "true"}}
            self.transport.startTLS(tls_options("127.0.0.1", self.roots))
        elif "Admission" in message:
            assert self.secured and message["Admission"] == {"accepted": True, "username": self.admission["username"], "room": self.admission["room"]}
            self.send({"Hello": {"username": self.admission["username"], "room": {"name": self.admission["room"]},
                                 "version": "1.2.255", "realversion": "1.7.5", "features": {"uiMode": "cli"}}})
        elif "Hello" in message:
            assert self.secured and message["Hello"]["username"] == self.admission["username"]
            self.logged = True

    def connectionLost(self, reason):
        self.lost = True


class SyntheticAuthority:
    def __init__(self):
        self.pending = {}
        self.active = set()
        self.requests = 0
        self.available = True

    def issue(self, member, username, room, seconds=9):
        ticket = secrets.token_hex(32).upper()
        self.pending[ticket] = (member, username, room, seconds)
        return {"ticket": ticket, "clientRunId": "run-" + member, "username": username, "room": room}

    def admit(self, ticket, run, username, room):
        self.requests += 1
        member, expected_name, expected_room, seconds = self.pending.pop(ticket)
        if (run, username, room) != ("run-" + member, expected_name, expected_room):
            raise ValueError("admission rejected")
        self.active.add(member)
        return AdmissionLease(secrets.token_hex(16), member, "epoch-" + room, run, username, room, time.monotonic() + seconds)

    def is_active(self, lease):
        return self.available and lease.member_id in self.active

    def release(self, lease):
        # Membership in this fixture is independent of a single transport.
        pass


def run_admission(root):
    failures, clients = [], []
    report = {"realTls": True, "realPinnedServer": True, "syntheticAuthorityOnly": True,
              "nativePlayerAcceptance": False, "cases": {}}
    now = datetime.datetime.now(datetime.timezone.utc)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SyncWatch admission fixture")])
    ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
          .not_valid_after(now + datetime.timedelta(days=1)).add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .sign(key, hashes.SHA256()))
    roots = [ca.public_bytes(serialization.Encoding.PEM)]
    directory = root / "admission-tls"
    certificate_set(directory, key, ca)
    authority = SyntheticAuthority()
    factory = AdmissionFactory(authority, tls_directory=directory, salt="admission-fixture-salt")
    listener = reactor.listenTCP(0, factory, interface="127.0.0.1")
    port = listener.getHost().port

    @defer.inlineCallbacks
    def connect(admission=None, cleartext=None):
        client = yield protocol.ClientCreator(reactor, AdmissionProbe, roots, admission, cleartext).connectTCP("127.0.0.1", port)
        clients.append(client)
        return client

    @defer.inlineCallbacks
    def rejected(frame, label, *, encoded=False):
        packet = authority.issue(label, "u" + label, "room-a")
        client = yield connect(packet)
        yield wait_for(lambda: client.logged, label + " admission")
        server = factory.members[("epoch-room-a", label)]
        watcher = server._watcher
        previous = watcher.getFile()
        if encoded:
            client.sendLine(frame)
        else:
            client.send(frame)
        yield wait_for(lambda: client.lost and server not in factory.connections, label + " rejection/reap")
        assert watcher.getFile() == previous
        report["cases"][label] = True

    @defer.inlineCallbacks
    def scenario():
        try:
            clear = yield connect(cleartext={"Hello": {"username": "untrusted", "room": {"name": "room-a"}, "version": "1.2.255"}})
            yield wait_for(lambda: clear.lost, "pre-TLS Hello rejection")
            assert authority.requests == 0 and not factory.members
            report["cases"]["noCleartextAdmission"] = True
            forged = yield connect({"ticket": "A" * 64, "clientRunId": "run-a", "username": "ua", "room": "room-a"})
            yield wait_for(lambda: forged.lost, "forged ticket")
            assert not factory.members
            report["cases"]["forgedTicket"] = True

            packet = authority.issue("a", "ua", "room-a")
            a = yield connect(packet)
            b = yield connect(authority.issue("b", "ub", "room-a"))
            c = yield connect(authority.issue("c", "uc", "room-c"))
            yield wait_for(lambda: a.logged and b.logged and c.logged, "three admitted probes")
            a.send({"List": {}}); c.send({"List": {}})
            yield wait_for(lambda: any("List" in message for message in a.messages) and any("List" in message for message in c.messages), "isolated lists")
            assert [message["List"] for message in a.messages if "List" in message][-1].keys() == {"room-a"}
            assert [message["List"] for message in c.messages if "List" in message][-1].keys() == {"room-c"}
            assert factory.isolateRooms and not c.lost
            report["cases"]["isolatedLists"] = True
            a.send({"Set": {"file": {"name": "film-\u03bc.mkv", "duration": 600, "size": 4096}}})
            a.send({"Set": {"ready": {"isReady": True, "manuallyInitiated": False}}})
            yield wait_for(lambda: factory.members[("epoch-room-a", "a")]._watcher.getFile() is not None
                           and factory.members[("epoch-room-a", "a")]._watcher.isReady(), "authorized file/readiness")
            assert factory.members[("epoch-room-a", "a")]._watcher.getFile()["name"] == "film-\u03bc.mkv"
            a.send({"State": {"playstate": {"position": 90, "paused": True, "doSeek": True}}})
            yield wait_for(lambda: any(abs(message.get("State", {}).get("playstate", {}).get("position", 0) - 90) < 1 for message in b.messages), "upstream canonical seek")
            assert not any(abs(message.get("State", {}).get("playstate", {}).get("position", 0) - 90) < 1 for message in c.messages)
            report["cases"]["canonicalStatePreserved"] = True
            replay = yield connect(packet)
            yield wait_for(lambda: replay.lost, "consumed ticket replay")
            assert not a.lost and len(factory.members) == 3
            report["cases"]["singleUseTicket"] = True
            duplicate = yield connect(authority.issue("a", "ua", "room-a"))
            yield wait_for(lambda: duplicate.lost, "duplicate live transport")
            assert not a.lost and len(factory.members) == 3
            report["cases"]["liveTransportCannotBeReplaced"] = True

            yield rejected({"Set": {"file": {"name": "must-not-apply"}, "ready": {"isReady": True, "username": "ua"}}}, "foreign-ready")
            yield rejected({"Set": {"file": {"name": "must-not-apply"}, "room": {"name": "room-c"}}}, "room-switch")
            yield rejected({"Set": {"file": {"name": "must-not-apply"}, "controllerAuth": {"room": "room-c", "password": "untrusted"}}}, "controller")
            yield rejected({"Set": {"file": {"name": "must-not-apply"}}, "List": {}}, "mixed-frame")
            yield rejected(b'{"Set":{"file":{"name":"must-not-apply"}},"Set":{"ready":{"isReady":true}}}', "duplicates", encoded=True)
            yield rejected(b'{"State":{"playstate":{"position":1e999,"paused":false}}}', "nonfinite", encoded=True)
            yield rejected({"Set": {"ready": {"isReady": 1}}}, "ready-type")
            yield rejected({"Set": {"file": {"name": "local", "duration": 1, "size": 1, "path": "/private/movie"}}}, "private-path")
            yield rejected({"TLS": {"startTLS": "send"}}, "repeated-tls")

            server_a = factory.members[("epoch-room-a", "a")]
            renewed = AdmissionLease(server_a._lease.lease_id, "a", "epoch-room-a", "run-a", "ua", "room-a", time.monotonic() + 9)
            assert factory.renew(renewed) and server_a._lease == renewed
            stale = AdmissionLease("old-transport", "a", "epoch-room-a", "run-a", "ua", "room-a", time.monotonic() + 9)
            assert not factory.renew(stale) and not a.lost and server_a._lease == renewed
            report["cases"]["boundedLeaseRenewal"] = True
            authority.active.remove("b")
            factory.revoke("epoch-room-a", "b")
            yield wait_for(lambda: b.lost, "live revocation")
            assert not a.lost and not c.lost
            report["cases"]["revocationIsolation"] = True
            expiring = yield connect(authority.issue("short", "ushort", "room-short", seconds=0.2))
            yield wait_for(lambda: expiring.logged, "short admission")
            yield wait_for(lambda: expiring.lost, "unrenewed authority lease")
            report["cases"]["leaseExpiry"] = True
            authority.available = False
            yield wait_for(lambda: a.lost and c.lost, "authority channel lost")
            report["cases"]["authorityLossFailsClosed"] = True
        except Exception as error:
            failures.append(error)
        finally:
            factory.close()
            for client in clients:
                if not client.lost:
                    client.transport.abortConnection()
            yield listener.stopListening()
            try:
                yield wait_for(lambda: all(client.lost for client in clients) and not factory.connections and not factory.members, "admission group cleanup")
            except Exception as error:
                failures.append(error)
            finally:
                reactor.stop()

    reactor.callWhenRunning(scenario)
    reactor.run(installSignalHandlers=False)
    if failures:
        raise failures[0]
    print("SYNCWATCH_ADMISSION_PROOF " + json.dumps(report, sort_keys=True))


run_admission(Path(OWNED_ROOT))
