"""Authenticated STARTTLS with no cleartext Hello or downgrade path."""

from twisted.internet import reactor
from twisted.internet.error import ConnectionDone, ConnectionLost, TCPTimedOutError, TimeoutError
from twisted.internet.ssl import Certificate, TLSVersion, optionsForClientTLS, trustRootFromCertificates
from syncplay.client import SyncClientFactory
from syncplay.protocols import SyncClientProtocol


def tls_options(hostname, certificates):
    if not certificates:
        raise ValueError("An explicit owned trust root is required")
    roots = trustRootFromCertificates([Certificate.loadPEM(value) for value in certificates])
    return optionsForClientTLS(hostname, trustRoot=roots,
        extraCertificateOptions={"raiseMinimumTo": TLSVersion.TLSv1_2})


def loss_code(phase, reason):
    # A socket may close before STARTTLS completes during a temporary outage.
    # Only typed transport failures can retry; TLS verification/protocol errors
    # and an explicit rejection stay terminal regardless of connection history.
    if phase != "failed" and reason.check(ConnectionDone, ConnectionLost, TCPTimedOutError, TimeoutError):
        return "connection_lost"
    return "tls_handshake_failed"


class StrictClientProtocol(SyncClientProtocol):
    def __init__(self, client):
        super().__init__(client)
        self._phase = "new"
        self._deadline = None
        self._cancel_admission = None

    def connectionMade(self):
        self._client.initProtocol(self)
        self._phase = "requested"
        self.sendTLS({"startTLS": "send"})
        self._deadline = reactor.callLater(self._client.handshake_timeout, self._reject, "tls_timeout")

    def _cancel_deadline(self):
        if self._deadline is not None and self._deadline.active():
            self._deadline.cancel()

    def _reject(self, code):
        if self._phase == "failed":
            return
        self._phase = "failed"
        self._cancel_deadline()
        self._clear_admission()
        self._client.transport_failed(code)
        self.abort()

    def handleMessages(self, messages):
        if self._phase == "admitting":
            expected = {"Admission": {"accepted": True, "username": self._client.getUsername(), "room": self._client.getRoom()}}
            if messages != expected or type(messages.get("Admission", {}).get("accepted")) is not bool:
                self._reject("admission_rejected")
                return
            self._phase = "secured"
            self._cancel_deadline()
            super().sendHello()
        elif not isinstance(messages, dict):
            self._reject("malformed_syncplay_message")
        elif self._phase != "secured" and (self._phase != "requested" or set(messages) != {"TLS"}):
            self._reject("unauthenticated_syncplay_message")
        else:
            super().handleMessages(messages)

    def handleTLS(self, message):
        if (self._phase != "requested" or not isinstance(message, dict)
                or set(message) != {"startTLS"} or message.get("startTLS") != "true"):
            self._reject("tls_required")
            return
        self._phase = "handshaking"
        self.transport.startTLS(self._client.protocolFactory.options)

    def handshakeCompleted(self):
        if self._phase != "handshaking" or self.transport.getPeerCertificate() is None:
            self._reject("tls_certificate_missing")
            return
        # Twisted invokes this callback only after chain and service identity
        # verification by the explicit optionsForClientTLS context above.
        self._phase = "secured"
        self._cancel_deadline()
        super().handshakeCompleted()

    def sendHello(self):
        if self._phase != "secured":
            self._reject("cleartext_hello_forbidden")
            return
        if self._client.admission is None:
            super().sendHello()
        else:
            self._phase = "ticket"
            self._deadline = reactor.callLater(self._client.handshake_timeout, self._reject, "admission_timeout")
            self._cancel_admission = self._client.admission(self._admit)

    def _admit(self, credential):
        if self._phase != "ticket":
            raise ValueError("stale admission transport")
        self._clear_admission()
        self._phase = "admitting"
        self.sendMessage({"Admission": credential | {"username": self._client.getUsername(), "room": self._client.getRoom()}})

    def _clear_admission(self):
        cancel, self._cancel_admission = self._cancel_admission, None
        if cancel is not None:
            cancel()

    def connectionLost(self, reason):
        phase, self._phase = self._phase, "lost"
        self._cancel_deadline()
        self._clear_admission()
        super().connectionLost(reason)
        self._client.transport_failed(loss_code(phase, reason))


class StrictClientFactory(SyncClientFactory):
    def buildProtocol(self, address):
        self.options = self._client.tls_options
        if self.options is None:
            raise ValueError("Authenticated TLS context is required")
        return StrictClientProtocol(self._client)
