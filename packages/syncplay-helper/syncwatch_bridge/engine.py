"""Bind the pipe controller to the real upstream engine; no synchronization policy."""

import secrets

from syncwatch_ipc.contracts import ContractError


class PlayerPort:
    def __init__(self, controller):
        self.controller = controller
        self.observer = None
        self.state = None
        self.released = False

    def request_state(self, callback):
        self.observer = callback

    def state_pending(self):
        return bool(self.controller.pending)

    def observe(self, state, *, notify=True, user_initiated=False):
        self.state = dict(state)
        if notify and self.observer is not None and not self.released:
            self.observer(state["paused"], state["positionMs"], user_initiated=user_initiated)

    def set_paused(self, value):
        self.controller.request("player.setPaused", {"paused": value})

    def set_position(self, value):
        self.controller.request("player.setPosition", {"positionMs": value})

    def set_rate(self, value):
        self.controller.request("player.setSpeed", {"rate": value})

    def open_media(self, handle, reset):
        self.controller.request("player.openFile", {"mediaHandle": handle})
        if reset:
            self.set_position(0)

    def release(self):
        self.released = True
        self.observer = None


class Engine:
    def __init__(self, controller, *, trust_roots=None):
        self.controller, self.trust_roots = controller, trust_roots
        self.session = None
        self.pending_admission = None
        self.port = None
        self.media = None
        self.status = None
        self.last_participants = None
        self.last_ready = None
        self.last_identity = None
        self.established = False
        self.terminal = False

    def connect(self, payload):
        from syncwatch_syncplay.session import Session
        from syncplay import constants
        if self.session is not None:
            raise ContractError("invalidState")
        self.established = self.terminal = False
        self.last_identity = self.last_ready = self.last_participants = None
        self.port = PlayerPort(self.controller)
        self.session = Session(payload["host"], payload["port"], payload["username"], payload["room"],
                               self.port, self.event, trust_roots=self.trust_roots,
                               admission=self.request_admission if payload.get("accountAdmission") is True else None)
        # The application selection coordinator owns native media. The stock
        # playlist must not independently reopen/rewind an already owned file.
        self.session.getConfig()["sharedPlaylistEnabled"] = False
        # The application's play button is an explicit shared playback action,
        # not the stock client's combined play/readiness toggle. Use the upstream
        # policy for direct unpause; leave all timing/correction logic upstream.
        self.session.getConfig()["unpauseAction"] = constants.UNPAUSE_ALWAYS_MODE
        if self.media is not None:
            self.apply_media()
        self.connection("connecting")
        self.session.connect()

    def request_admission(self, consume):
        if self.pending_admission is not None or self.session is None or self.terminal:
            raise ContractError("invalidState")
        nonce = secrets.token_hex(16)
        self.pending_admission = (nonce, consume)
        self.controller.event("sync.admissionRequired", {"nonce": nonce})
        def cancel():
            if self.pending_admission == (nonce, consume):
                self.pending_admission = None
        return cancel

    def admit(self, payload):
        pending = self.pending_admission
        if pending is None or pending[0] != payload["nonce"] or self.session is None or self.terminal:
            raise ContractError("invalidState")
        # A ticket is transferred once, never reused by a reconnecting protocol.
        self.pending_admission = None
        pending[1]({"ticket": payload["ticket"], "clientRunId": payload["clientRunId"]})

    def event(self, kind, payload):
        # Raw upstream text may contain room/user/file data. It is never
        # forwarded to stderr or mistaken for a wire frame.
        if kind == "sync.disconnected":
            self.last_identity = None
            # Retry timing belongs to upstream ClientService. Only an ordinary
            # loss of an established authenticated transport is recoverable.
            # A TLS/protocol rejection followed by connectionLost stays fatal.
            if payload.get("code") != "connection_lost" or not self.established:
                self.terminal = True
            self.connection("disconnected" if self.terminal else "connecting")
            self.last_ready = False
            self.last_participants = []
            self.controller.event("sync.readiness", {"ready": False})
            self.controller.event("sync.participants", {"participants": []})

    def connection(self, state):
        if self.status != state:
            self.status = state
            self.controller.event("sync.connection", {"state": state})

    def loaded(self, payload):
        if self.media is not None:
            raise ContractError("invalidState")
        self.media = dict(payload)
        if self.session is not None:
            self.apply_media()

    def apply_media(self):
        m = self.media
        self.session.media_loaded(m["mediaHandle"], m["name"], m["durationMs"], m["fileSizeBytes"])

    def observe(self, state, *, user_initiated=False):
        if self.port is None or self.media is None:
            raise ContractError("invalidState")
        if state["durationMs"] != self.media["durationMs"]:
            raise ContractError("conflict")
        protocol = self.session._protocol if self.session is not None else None
        self.port.observe(state, notify=bool(protocol and protocol.logged), user_initiated=user_initiated)

    def player_result(self, result):
        if self.port is None:
            raise ContractError("invalidState")
        if self.port.state is None:
            return
        if result["positionMs"] > self.media["durationMs"]:
            raise ContractError("conflict")
        # The correlated result contains actual native pause/position, not
        # expected command values. Update engine observations with no user
        # authority. Omit stale heartbeat state until every correction is acked.
        state = self.port.state | {"positionMs": result["positionMs"], "paused": result["paused"]}
        self.observe(state, user_initiated=False)

    def ready(self, value):
        if (self.terminal or self.status != "connected" or self.session is None
                or not self.session.has_initial_state or self.port.state is None
                or self.port.state["status"] != "ready"):
            raise ContractError("invalidState")
        self.session.changeReadyState(value)

    def tick(self):
        session = self.session
        if session is None or self.terminal or not session.has_initial_state:
            return
        if session.has_initial_state:
            # getUsername is updated by the authenticated upstream Hello, including
            # a server-assigned suffix. The entered display name is not identity.
            username = session.getUsername()
            if username != self.last_identity:
                self.controller.event("sync.identity", {"username": username})
                self.last_identity = username
            self.connection("connected")
            self.established = True
        ready = session.userlist.currentUser.isReady() is True
        if ready != self.last_ready:
            self.last_ready = ready
            self.controller.event("sync.readiness", {"ready": ready})
        users = [session.userlist.currentUser, *session.userlist._users.values()]
        participants = [{"username": user.username, "ready": user.isReady() is True}
                        for user in users if user.room == session.userlist.currentUser.room]
        if len(participants) > 64:
            raise ContractError("queueFull")
        if participants != self.last_participants:
            self.last_participants = participants
            self.controller.event("sync.participants", {"participants": participants})

    def disconnect(self, done):
        session, self.session = self.session, None
        self.pending_admission = None
        if session is None:
            done()
        else:
            session.close().addBoth(lambda _: done())
        self.connection("disconnected")

    def close(self, done):
        self.disconnect(done)
