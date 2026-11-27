"""Exercise actual public APIs using the owned embedded interpreter only."""

import syncwatch_syncplay
from importlib.util import find_spec
import ssl
import platform
from pathlib import Path
import certifi
import cryptography
import OpenSSL
import attrs
from cffi import FFI
from cryptography.hazmat.primitives import hashes

assert platform.python_implementation() == "CPython"
assert platform.python_version() == "3.12.15"
assert ssl.OPENSSL_VERSION.startswith("OpenSSL 3.5.9")
assert cryptography.__version__ == "50.0.2"
assert OpenSSL.__version__ == "26.4.0"
assert attrs.__version__ == "26.1.0"
assert Path(certifi.where()).resolve().is_relative_to(Path(EXPECTED_PREFIX).resolve())
digest = hashes.Hash(hashes.SHA256())
digest.update(b"abc")
assert digest.finalize().hex() == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
ffi = FFI()
callback = ffi.callback("int(int)", lambda value: value + 1)
assert callback(41) == 42
from syncwatch_syncplay.reactor import install
install()
from syncwatch_syncplay.session import Session
assert Session.ownsReactor is False
assert find_spec("syncplay.players") is None

from twisted.internet.error import ConnectionDone, ConnectionLost, TimeoutError, VerifyError
from twisted.python.failure import Failure
from OpenSSL.SSL import Error as TLSError
from syncwatch_syncplay.transport import loss_code
for phase in ("requested", "handshaking", "secured"):
    for error in (ConnectionDone(), ConnectionLost(), TimeoutError()):
        assert loss_code(phase, Failure(error)) == "connection_lost"
    for error in (VerifyError(), TLSError(), ValueError("invalid TLS handshake")):
        assert loss_code(phase, Failure(error)) == "tls_handshake_failed"
assert loss_code("failed", Failure(ConnectionLost())) == "tls_handshake_failed"
