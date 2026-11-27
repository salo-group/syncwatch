"""Translate the upstream seconds API to the native player's millisecond port."""

import math


class BridgePlayer:
    speedSupported = True
    alertOSDSupported = True
    chatOSDSupported = False
    osdMessageSeparator = "\n"

    def __init__(self, client, port):
        self.client = client
        self.port = port
        self.handles = {}

    def askForStatus(self):
        self.port.request_state(self.observe)

    def observe(self, paused, position_ms, *, user_initiated=False):
        self.client.assert_owner_thread()
        if type(paused) is not bool or type(position_ms) is not int or position_ms < 0:
            raise ValueError("Native playback state requires a pause flag and nonnegative integer milliseconds")
        # An asynchronous native pause/result may arrive during TLS recovery.
        # The upstream status API can toggle readiness and requires a live
        # authenticated protocol; retain native state in its owner until then.
        protocol = self.client._protocol
        if protocol is None or not protocol.logged:
            return
        self.client.updatePlayerStatus(paused, position_ms / 1000.0, fromUser=user_initiated)

    def setPaused(self, value):
        if type(value) is not bool:
            raise ValueError("Invalid engine pause correction")
        self.port.set_paused(value)

    def setPosition(self, value):
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError("Invalid engine position correction")
        self.port.set_position(round(value * 1000))

    def setSpeed(self, value):
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError("Invalid local engine rate correction")
        self.port.set_rate(value)

    def openFile(self, name, resetPosition=False):
        handle = self.handles.get(name)
        if handle is None:
            self.client.events("media.unavailable", {"name": name})
            return
        self.port.open_media(handle, resetPosition)

    def displayMessage(self, message, duration=0, OSDType=None, mood=None):
        self.client.events("playback.notice", {"message": message})

    def setFeatures(self, features):
        self.client.events("sync.features", dict(features))

    def drop(self):
        self.port.release()
