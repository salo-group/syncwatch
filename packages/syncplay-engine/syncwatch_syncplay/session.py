"""Own a real upstream engine session on the helper's reactor thread."""

import copy
import json
import threading
from pathlib import Path

import certifi
import pem
from twisted.internet import defer
from syncplay import constants
from syncplay.client import SyncplayClient

from .player import BridgePlayer
from .reactor import install
from .transport import StrictClientFactory, tls_options
from .ui import BridgeUI


def configuration(host, port, name, room):
    values = json.loads(Path(__file__).with_name("defaults.json").read_text(encoding="utf-8"))
    values = {key: copy.deepcopy(getattr(constants, value["constant"]))
              if isinstance(value, dict) and set(value) == {"constant"} else copy.deepcopy(value)
              for key, value in values.items()}
    values.update(host=host, port=port, name=name, room=room, noGui=True, noStore=True,
                  checkForUpdatesAutomatically=False, mediaSearchDirectories=[],
                  showOSD=False, chatOutputEnabled=False, readyAtStart=False)
    return values


class Session(SyncplayClient):
    protocolFactoryType = StrictClientFactory
    ownsReactor = False
    handshake_timeout = 10.0

    def __init__(self, host, port, name, room, player_port, events, *, trust_roots=None):
        install()
        self._owner_thread = threading.get_ident()
        if not isinstance(host, str) or not host or type(port) is not int or not 0 < port < 65536:
            raise ValueError("Invalid Syncplay endpoint")
        self.events = events
        certificates = [str(value).encode("ascii") for value in pem.parse_file(certifi.where())]
        # Fixtures may supply owned CA certificates, never a replacement TLS
        # context that could bypass endpoint verification or the TLS minimum.
        self.tls_options = tls_options(host, certificates if trust_roots is None else trust_roots)
        self.endpoint_host, self.endpoint_port = host, port
        self._closing = False
        super().__init__(None, BridgeUI(events), configuration(host, port, name, room))
        self.fileSwitch.folderSearchEnabled = False
        self.bridge_player = BridgePlayer(self, player_port)
        self.initPlayer(self.bridge_player)

    def initProtocol(self, protocol):
        self.assert_owner_thread()
        super().initProtocol(protocol)
        # Upstream stamps the previous global state when registering a protocol.
        # Our native player survives transports, so that timestamp would prevent
        # upstream's first-state initialization when a surviving peer is ahead.
        # Require this transport's actual server snapshot; its ordinary ordered
        # native corrections and acknowledgements remain the only timing path.
        self._lastGlobalUpdate = None

    def connect(self):
        self.assert_owner_thread()
        self.start(self.endpoint_host, self.endpoint_port)

    def assert_owner_thread(self):
        if threading.get_ident() != self._owner_thread:
            raise RuntimeError("Syncplay session requires its owning reactor thread")

    @property
    def has_initial_state(self):
        return bool(self._protocol and self._protocol.logged and self._protocol.hadFirstStateUpdate
                    and self._lastPlayerUpdate is not None)

    def getLocalState(self):
        # An asynchronous native correction is not applied until acknowledged.
        # Keep upstream heartbeats, but omit a stale playstate while its native
        # owner is applying the ordered correction. Timing decisions stay in
        # the upstream engine; expected command values are not observations.
        if self.bridge_player.port.state_pending():
            return None, None, None, None
        return super().getLocalState()

    def media_loaded(self, handle, name, duration_ms, size):
        self.assert_owner_thread()
        if (not isinstance(handle, str) or not handle or any(c in handle for c in "/\\:\x00")
                or not isinstance(name, str) or not name or any(c in name for c in "/\\\x00")
                or type(duration_ms) is not int or duration_ms < 0 or type(size) is not int or size < 0):
            raise ValueError("Invalid authorized native media descriptor")
        self.bridge_player.handles = {handle: handle, name: handle}
        self.updateFile(name, duration_ms / 1000.0, handle, fileSize=size)

    def transport_failed(self, code):
        if self._closing:
            return
        self.userlist.currentUser.setReady(False)
        if self._player is not None:
            self._player.setPaused(True)
        if not self._closing:
            self.events("sync.disconnected", {"code": code})

    def close(self):
        self.assert_owner_thread()
        if self._closing:
            return defer.succeed(None)
        self._closing = True
        self.userlist.currentUser.setReady(False)
        self.bridge_player.setPaused(True)
        service = getattr(self, "_reconnectingService", None)
        result = defer.maybeDeferred(service.stopService) if service is not None else defer.succeed(None)
        self.stop()
        timers = [self._askPlayerTimer, self.autoplayTimer, self.fileSwitch.fileSwitchTimer,
                  self._warnings.pausedTimer, *(v["timer"] for v in self._warnings._warnings.values())]
        for timer in timers:
            if timer is not None and timer.running:
                timer.stop()
        return result
