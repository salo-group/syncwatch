"""Two real helper processes and inherited pipes against a real TLS Syncplay server."""

import os
import shutil
import subprocess
import queue


class PipePlayer(SimulatedPlayer):
    def __init__(self, runtime, name):
        super().__init__()
        self.name = name
        self.sequence = 0
        self.user_sequence = 0
        self.events = []
        self.hold_corrections = False
        self.held = []
        self.cancelled_corrections = 0
        self.started = time.monotonic()
        self.closing = False
        self.replies = {}
        self.frames = queue.Queue(maxsize=128)
        executable = runtime / "bin" / ("syncwatch-helper.exe" if os.name == "nt" else "syncwatch-helper")
        self.process = subprocess.Popen([str(executable), str(runtime)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        def read():
            while not self.closing:
                frame = self.process.stdout.readline(65537)
                while not self.closing:
                    try:
                        self.frames.put(frame, timeout=0.1)
                        break
                    except queue.Full:
                        pass
                if not frame:
                    return
        self.reader = threading.Thread(target=read)
        self.reader.start()
        self.send("bridge.hello", {"supportedVersions": [1]})

    def send(self, kind, payload, *, reply=None, user=False):
        self.sequence += 1
        value = {"v": 1, "type": kind, "clientRunId": self.name, "mediaGeneration": 2, "payload": payload,
                 "replyTo" if reply else "id": reply or "cmd-" + str(self.sequence)}
        if kind == "player.state":
            value["origin"] = "user" if user else "observation"
            if user:
                self.user_sequence += 1
                value["actionSequence"] = self.user_sequence
        self.process.stdin.write((json.dumps(value) + "\n").encode())
        self.process.stdin.flush()

    def tick(self):
        while (self.held and not self.hold_corrections) or not self.frames.empty():
            frame = self.held.pop(0) if self.held and not self.hold_corrections else self.frames.get_nowait()
            if not frame:
                diagnostic = self.process.stderr.read(4096).decode("utf-8", errors="replace")
                code = self.process.wait(timeout=1)
                raise AssertionError("Real helper exited unexpectedly: " + self.name + " exit=" + str(code)
                                     + " queued=" + str(self.frames.qsize()) + " " + diagnostic)
            value = json.loads(frame)
            kind, payload = value["type"], value["payload"]
            if "replyTo" in value:
                assert kind != "bridge.error", payload
                self.replies[value["replyTo"]] = kind
            elif kind.startswith("player."):
                if self.hold_corrections:
                    assert len(self.held) < 64
                    self.held.append(frame)
                    continue
                if value.get("afterUserAction", 0) < self.user_sequence:
                    self.cancelled_corrections += 1
                    self.send("bridge.error", {"code": "cancelled", "message": "cancelled", "retryable": False}, reply=value["id"])
                    continue
                if kind == "player.setPaused": self.set_paused(payload["paused"])
                elif kind == "player.setPosition": self.set_position(payload["positionMs"])
                elif kind == "player.setSpeed": self.set_rate(payload["rate"])
                elif kind == "player.openFile": self.open_media(payload["mediaHandle"], False)
                else: raise AssertionError("Unexpected player request")
                self.send("player.result", {"status": "applied", "positionMs": self.current(), "paused": self.paused}, reply=value["id"])
            else:
                self.events.append((kind, payload))
        if any(kind == "sync.connection" for kind, _ in self.events):
            self.publish()

    def publish(self, *, user=False):
        self.send("player.state", {"status": "ready", "positionMs": self.current(), "durationMs": 600000,
            "paused": self.paused, "rate": self.rate, "seekable": True}, user=user)

    def connected(self):
        return any(kind == "sync.connection" and p["state"] == "connected" for kind, p in self.events)

    def close(self):
        self.closing = True
        forced = False
        if self.process.poll() is None:
            self.process.stdin.close()
            try: self.process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                forced = True
                self.process.kill()
                self.process.wait(timeout=4)
        self.reader.join(timeout=4)
        assert not self.reader.is_alive(), "Owned helper reader did not join"
        self.process.stdout.close()
        self.process.stderr.close()
        return {"exit": self.process.returncode, "forcedStop": forced, "readerJoined": True}


def bridge_run(root):
    runtime = root / "helper-\u03bc-\u0442\u0435\u0441\u0442"
    shutil.copytree(BRIDGE_RUNTIME, runtime)
    print("PIPE_FIXTURE relocated", flush=True)
    now = datetime.datetime.now(datetime.timezone.utc)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SyncWatch pipe fixture CA")])
    ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
          .not_valid_after(now + datetime.timedelta(days=1)).add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
          .sign(key, hashes.SHA256()))
    # Explicit test-only derivative: add the controlled server CA to the copied
    # trust store. The deliverable and its original certifi bytes stay unchanged.
    trust_store = next(runtime.glob("**/certifi/cacert.pem"))
    with trust_store.open("ab") as stream: stream.write(b"\n" + ca.public_bytes(serialization.Encoding.PEM))
    directory = root / "pipe-server"
    certificate_set(directory, key, ca)
    factory = ObservedFactory(port="0", salt="pipe-fixture", tlsCertPath=str(directory))
    listener = reactor.listenTCP(0, factory, interface="127.0.0.1")
    players, failures = [], []
    scenario_worker = [None]
    report = {"realHelperProcesses": 2, "realUpstreamServer": True, "authenticatedTLS": True,
              "simulatedNativePlayers": True, "testOnlyCAAppend": True}
    loop = task.LoopingCall(lambda: [p.tick() for p in players])

    @defer.inlineCallbacks
    def scenario():
        try:
            for username in ["pipe-a", "pipe-b"]:
                player = PipePlayer(runtime, username)
                players.append(player)
                yield wait_for(lambda: "cmd-1" in player.replies, "pipe hello", timeout=15)
                report.setdefault("helperStartupMs", []).append(round((time.monotonic() - player.started) * 1000))
                print("PIPE_FIXTURE hello", username, flush=True)
                player.send("player.loaded", {"mediaHandle": "media-1", "name": "pipe-film.mkv",
                                             "durationMs": 600000, "fileSizeBytes": 4096})
                player.send("sync.connect", {"host": "127.0.0.1", "port": listener.getHost().port,
                    "room": "pipe-room", "username": username, "tls": True})
            yield wait_for(lambda: all(p.connected() for p in players), "two connected pipe clients")
            print("PIPE_FIXTURE connected", flush=True)
            for p in players: p.send("sync.setReady", {"ready": True})
            yield wait_for(lambda: all(any(k == "sync.readiness" and v["ready"] for k, v in p.events)
                                      for p in players), "pipe readiness")
            yield wait_for(lambda: all(any(k == "sync.participants" and len(v["participants"]) == 2
                and all(user["ready"] for user in v["participants"]) for k, v in p.events) for p in players),
                "all pipe participant readiness observations")
            print("PIPE_FIXTURE ready", flush=True)
            a, b = players
            a.tick()
            a.set_paused(False)
            a.publish(user=True)
            yield wait_for(lambda: not b.paused, "pipe shared play")
            print("PIPE_FIXTURE play", flush=True)
            # A delayed native correction/result is an observation, not a
            # second user pause. The engine must restore canonical playback.
            a.set_paused(True)
            a.publish(user=False)
            yield wait_for(lambda: not a.paused and not b.paused, "correction observation does not echo user pause")
            report["correctionDoesNotEcho"] = True
            # Hold a real older helper correction across a newer local seek.
            # Its causal watermark must prevent it undoing that user action.
            a.hold_corrections = True
            b.set_paused(True)
            b.publish(user=True)
            yield wait_for(lambda: bool(a.held), "held native correction")
            a.set_position(90000)
            a.publish(user=True)
            a.hold_corrections = False
            yield wait_for(lambda: b.current() > 85000 and abs(a.current() - b.current()) < 1500, "pipe shared seek")
            b.tick()
            b.set_paused(True)
            b.publish(user=True)
            yield wait_for(lambda: a.paused, "pipe shared pause")
            assert set(factory.hellos) == {"pipe-a", "pipe-b"}
            assert b'"Hello"' not in b"".join(factory.wire)
            assert a.cancelled_corrections > 0
            report.update(play=True, seek=True, pause=True, noPlaintextIdentity=True, staleCorrectionsCancelled=True)
            # Interrupt the real owned server-side TLS connection. Keep the
            # same helper process, pipes, media handle and command counters.
            before = len(a.events)
            process_id = a.process.pid
            factory.reject_new_connections = True
            reactor.callLater(3, setattr, factory, "reject_new_connections", False)
            factory.connections[0].transport.abortConnection()
            yield wait_for(lambda: any(k == "sync.connection" and v["state"] == "connecting"
                                      for k, v in a.events[before:]), "pipe recovery starts")
            # The surviving participant may choose a newer paused timeline while
            # this native player still observes its pre-interruption position.
            # Rejoining must initialize from the server instead of advertising
            # that stale position as the room's new slowest participant.
            b.set_position(150000)
            b.publish(user=True)
            yield wait_for(lambda: any(s.get("playstate", {}).get("position", 0) > 145
                                      for s in factory.states), "survivor seek reaches server")
            yield wait_for(lambda: any(k == "sync.connection" and v["state"] == "connected"
                                      for k, v in a.events[before:]), "pipe authenticated recovery", timeout=15)
            recovered = a.events[before:]
            assert factory.rejected_connections > 0
            assert not any(k == "sync.connection" and v["state"] == "disconnected" for k, v in recovered)
            assert ("sync.readiness", {"ready": False}) in recovered
            identity = ("sync.identity", {"username": "pipe-a"})
            connected = ("sync.connection", {"state": "connected"})
            assert recovered.index(identity) < recovered.index(connected)
            a.send("sync.setReady", {"ready": True})
            yield wait_for(lambda: any(k == "sync.readiness" and v["ready"]
                                      for k, v in a.events[before:]), "pipe recovery readiness")
            yield wait_for(lambda: a.current() > 145000 and b.current() > 145000,
                           "surviving newer timeline retained on recovery")
            # Room position is periodically derived from its slowest watcher.
            # Check beyond that update, not only the first reconnect frame.
            yield task.deferLater(reactor, 2, lambda: None)
            assert a.current() > 145000 and b.current() > 145000, "survivor timeline rolled back"
            report["survivorSeekDuringRecoveryRetained"] = True
            a.set_position(120000)
            a.publish(user=True)
            yield wait_for(lambda: b.current() > 115000 and abs(a.current() - b.current()) < 1500,
                           "pipe recovered seek")
            b.set_paused(False)
            b.publish(user=True)
            yield wait_for(lambda: not a.paused, "pipe recovered reverse play")
            b.set_paused(True)
            b.publish(user=True)
            yield wait_for(lambda: a.paused, "pipe recovered reverse pause")
            assert a.process.pid == process_id and a.process.poll() is None
            report.update(authenticatedRecovery=True, recoveryIdentityRetained=True,
                          recoveryReadinessConfirmed=True, recoveryBidirectionalPlayback=True,
                          helperProcessRetained=True, transientPreTlsClosesRecovered=True)
            a.send("sync.disconnect", {})
            yield wait_for(lambda: any(k == "sync.connection" and v["state"] == "disconnected"
                                      for k, v in a.events), "pipe disconnect")
            report["disconnect"] = True
        except Exception:
            print("PIPE_FAILURE", [{"events": p.events[-8:], "commands": p.commands[-8:], "paused": p.paused,
                "position": p.current(), "exit": p.process.poll()} for p in players], "server", factory.states[-4:], flush=True)
            from twisted.python.failure import Failure
            failures.append(Failure())
        finally:
            if loop.running:
                loop.stop()
            cleanup = []
            for p in players:
                try:
                    cleanup.append(p.close())
                except Exception:
                    from twisted.python.failure import Failure
                    failures.append(Failure())
            print("PIPE_FIXTURE cleanup " + json.dumps(cleanup, sort_keys=True), flush=True)
            yield defer.maybeDeferred(listener.stopListening)
            reactor.stop()
    def loop_failed(failure):
        failures.append(failure)
        # Cancellation enters the scenario's finally block and joins both real
        # helpers before stopping the reactor, including an early pipe EOF.
        if scenario_worker[0] is not None:
            scenario_worker[0].cancel()
        else:
            reactor.stop()
    def begin():
        scenario_worker[0] = scenario()
    loop.start(0.05).addErrback(loop_failed)
    reactor.callWhenRunning(begin)
    reactor.run(installSignalHandlers=False)
    if failures: failures[0].raiseException()
    print("SYNCWATCH_PIPE_ENGINE_PROOF " + json.dumps(report, sort_keys=True))


bridge_run(Path(OWNED_ROOT))
