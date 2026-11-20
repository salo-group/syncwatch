"""Actual upstream TLS acceptance using the shared controlled-server fixture."""

def run(root):
    failures, sessions, listeners = [], [], []
    report = {"realUpstreamServer": True, "realUpstreamClients": True, "simulatedPlayerOnly": True,
              "tls": {}, "playback": {}, "lifecycle": {}}
    now = datetime.datetime.now(datetime.timezone.utc)
    root_key = ec.generate_private_key(ec.SECP256R1())
    root_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SyncWatch acceptance root")])
    ca = (x509.CertificateBuilder().subject_name(root_name).issuer_name(root_name)
          .public_key(root_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=1))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True).sign(root_key, hashes.SHA256()))
    ca_pem = ca.public_bytes(serialization.Encoding.PEM)
    observations = []

    def client(port, name, *, trusted=True):
        player = SimulatedPlayer()
        events = []
        def receive(kind, value):
            events.append((kind, value))
            if kind == "sync.disconnected":
                observations.append((name, player.paused, session.userlist.currentUser.isReady()))
        session = Session("127.0.0.1", port, name, "acceptance-room", player, receive,
                          trust_roots=[ca_pem] if trusted else None)
        # Match the native helper: media selection is application-owned, not a
        # second stock-player playlist that may asynchronously reopen/rewind it.
        session.getConfig()["sharedPlaylistEnabled"] = False
        session.handshake_timeout = 2.0
        session.media_loaded("owned-handle-" + name, "acceptance-film.mkv", 600000, 4096)
        session.connect()
        sessions.append(session)
        return session, player, events

    def server(name, **kwargs):
        directory = root / name
        certificate_set(directory, root_key, ca, **kwargs)
        factory = ObservedFactory(port="0", salt="acceptance-fixture-salt", tlsCertPath=str(directory))
        assert factory.serverAcceptsTLS
        listener = reactor.listenTCP(0, factory, interface="127.0.0.1")
        listeners.append(listener)
        return factory, listener.getHost().port

    @defer.inlineCallbacks
    def scenario():
        try:
            factory, port = server("valid")
            a, pa, _ = client(port, "acceptance-a")
            b, pb, _ = client(port, "acceptance-b")
            yield wait_for(lambda: a.isConnectedAndInARoom() and b.isConnectedAndInARoom(), "two authenticated clients")
            yield wait_for(lambda: len(a.userlist._users) == 1 and len(b.userlist._users) == 1, "accepted participant identities")
            yield wait_for(lambda: a.has_initial_state and b.has_initial_state,
                           "initial canonical state and native observations")
            a.changeReadyState(True, manuallyInitiated=False)
            b.changeReadyState(True, manuallyInitiated=False)
            yield wait_for(lambda: a.userlist.areAllUsersInRoomReady() and b.userlist.areAllUsersInRoomReady(), "readiness")
            report["tls"]["authenticatedHello"] = set(factory.hellos) == {"acceptance-a", "acceptance-b"}
            report["playback"]["readiness"] = True
            wrong_thread = []
            def foreign_observation():
                try:
                    a.bridge_player.observe(False, 0)
                except RuntimeError:
                    wrong_thread.append(True)
            worker = threading.Thread(target=foreign_observation)
            worker.start()
            worker.join(timeout=1)
            assert not worker.is_alive() and wrong_thread == [True]
            report["lifecycle"]["foreignThreadRejected"] = True
            pa.set_paused(False)
            a.bridge_player.observe(False, pa.current(), user_initiated=True)
            yield wait_for(lambda: not pb.paused, "shared play")
            report["playback"]["play"] = True
            pa.set_position(90000)
            a.bridge_player.observe(False, pa.current(), user_initiated=True)
            yield wait_for(lambda: abs(pb.current() - pa.current()) < 1500 and pb.current() > 85000, "shared seek")
            report["playback"]["seek"] = True
            pb.set_paused(True)
            b.bridge_player.observe(True, pb.current(), user_initiated=True)
            yield wait_for(lambda: pa.paused and pb.paused and pa.current() > 85000 and pb.current() > 85000,
                           "shared pause at the observed seek position")
            report["playback"]["pause"] = True
            pb.set_paused(False)
            b.bridge_player.observe(False, pb.current(), user_initiated=True)
            yield wait_for(lambda: not pa.paused, "shared resume")
            pb.set_paused(True)
            b.bridge_player.observe(True, pb.current(), user_initiated=True)
            pb.set_position(145000)
            b.bridge_player.observe(True, pb.current(), user_initiated=True)
            yield wait_for(lambda: pa.paused and abs(pa.current() - 145000) < 1500,
                           "remote pause followed immediately by seek")
            report["playback"]["rapidPauseThenSeek"] = True
            pa.set_paused(False)
            a.bridge_player.observe(False, pa.current(), user_initiated=True)
            yield wait_for(lambda: not pb.paused, "resume before a rapid seek and pause")
            pa.set_position(205000)
            a.bridge_player.observe(False, pa.current(), user_initiated=True)
            pa.set_paused(True)
            after_pause = len(pa.commands)
            a.bridge_player.observe(True, pa.current(), user_initiated=True)
            yield wait_for(lambda: pb.paused and abs(pb.current() - 205000) < 1500,
                           "remote seek followed immediately by pause")
            assert pa.paused and ("pause", False) not in pa.commands[after_pause:]
            report["playback"]["rapidSeekThenPause"] = True
            late, pl, _ = client(port, "acceptance-late")
            yield wait_for(lambda: late.isConnectedAndInARoom() and abs(pl.current() - 205000) < 1500 and pl.paused,
                           "late join current state")
            report["playback"]["lateJoin"] = True
            before = len(factory.hellos)
            a._protocol.transport.abortConnection()
            yield wait_for(lambda: any(name == "acceptance-a" for name, _, _ in observations), "disconnect observation")
            a.bridge_player.observe(True, pa.current())
            yield wait_for(lambda: len(factory.hellos) > before and a.isConnectedAndInARoom(), "TLS reconnect")
            assert any(name == "acceptance-a" and paused and ready is False for name, paused, ready in observations)
            report["lifecycle"]["disconnectPausedNotReady"] = True
            report["lifecycle"]["authenticatedReconnect"] = True
            for label, kwargs in [("wrong-host", {"bad_name": True}), ("expired", {"expired": True}), ("untrusted", {})]:
                bad_factory, bad_port = server(label, **kwargs)
                bad, bad_player, events = client(bad_port, "rejected-" + label, trusted=label != "untrusted")
                yield wait_for(lambda: any(kind == "sync.disconnected" for kind, _ in events), label)
                assert not bad_factory.hellos and not bad.isConnectedAndInARoom() and bad_player.paused
                assert b'"Hello"' not in b"".join(bad_factory.wire)
                report["tls"][label] = True
                yield bad.close()
            plain = ObservedFactory(port="0", salt="acceptance-fixture-salt")
            listener = reactor.listenTCP(0, plain, interface="127.0.0.1")
            listeners.append(listener)
            bad, player, events = client(listener.getHost().port, "rejected-cleartext")
            yield wait_for(lambda: any(kind == "sync.disconnected" for kind, _ in events), "STARTTLS declined")
            assert not plain.hellos and player.paused
            assert b'"Hello"' not in b"".join(plain.wire)
            report["tls"]["declinedNoHello"] = True
            yield bad.close()
            for label, response in [("malformed", b'{"TLS":{"startTLS":true}}\n'), ("timeout", b"")]:
                received = []
                class UnauthenticatedPeer(protocol.Protocol):
                    def dataReceived(self, data):
                        received.append(data)
                        if response:
                            self.transport.write(response)
                hostile = protocol.Factory.forProtocol(UnauthenticatedPeer)
                listener = reactor.listenTCP(0, hostile, interface="127.0.0.1")
                listeners.append(listener)
                bad, player, events = client(listener.getHost().port, "rejected-" + label)
                bad.handshake_timeout = 0.3
                yield wait_for(lambda: any(kind == "sync.disconnected" for kind, _ in events), label)
                wire = b"".join(received)
                assert b'"Hello"' not in wire and b"acceptance-room" not in wire and player.paused
                report["tls"][label + "NoHello"] = True
                yield bad.close()
            report["playback"]["canonicalStateMessages"] = len(factory.states)
            assert len(factory.states) >= 3
            wire = b"".join(factory.wire)
            assert b'"Hello"' not in wire and b"acceptance-room" not in wire and b"acceptance-a" not in wire
            report["tls"]["noPlaintextIdentityOrRoom"] = True
        except BaseException:
            print("ENGINE_FAILURE_STATE " + json.dumps([{
                "name": s.getUsername(), "playerPaused": s.bridge_player.port.paused, "playerPosition": s.bridge_player.port.current(),
                "globalPosition": s._globalPosition,
                "globalPaused": s._globalPaused, "storedPaused": s._playerPaused,
                "ready": s.userlist.currentUser.isReady(), "features": s.serverFeatures,
                "lastPlayerUpdate": s._lastPlayerUpdate,
            } for s in sessions], sort_keys=True))
            from twisted.python.failure import Failure
            failures.append(Failure())
        finally:
            yield defer.DeferredList([session.close() for session in sessions], consumeErrors=True)
            yield defer.DeferredList([defer.maybeDeferred(listener.stopListening) for listener in listeners], consumeErrors=True)
            report["lifecycle"]["ownedPlayersReleased"] = all(s.bridge_player.port.released
                and s.bridge_player.port.release_count == 1 for s in sessions)
            reactor.stop()

    reactor.callWhenRunning(scenario)
    reactor.run(installSignalHandlers=False)
    if failures:
        failures[0].raiseException()
    assert all(report["tls"].values()) and report["lifecycle"]["ownedPlayersReleased"]
    print("SYNCWATCH_ENGINE_PROOF " + json.dumps(report, sort_keys=True))
    return report


run(Path(OWNED_ROOT))
