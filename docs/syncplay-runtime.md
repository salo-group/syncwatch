# Headless Syncplay runtime

The runtime uses the pinned upstream Syncplay engine, Twisted's select reactor,
PyOpenSSL, cryptography and a source-built CPython interpreter. It has no Qt
dependency. Native player state crosses the application bridge; synchronization
and drift decisions remain in Syncplay.

The first-party player adapter implements the engine's player interface.
The runtime keeps the stock player modules only in the original source archive.

The [source graph](../tools/syncplay-runtime/graph.json) defines the additional
Git source authorities, original notice hashes, Rust crate locations and TLS
configuration. The existing embedded interpreter and engine source components
supply the base source profiles. This aggregate runtime is under development;
source registration does not enable the application provider.

Every application library is built from its declared Git submodule. Cargo must
run offline with an empty registry cache and resolve each enabled crate to its
declared original repository and package directory. A matching package version
alone does not establish source identity. Python packaging backends, wheels,
registry downloads and installed Python packages are not runtime sources.

TLS must validate both the certificate chain and endpoint identity. The selected
minimum is TLS 1.2. A missing TLS capability, declined STARTTLS request, invalid
certificate or malformed negotiation must terminate the connection before a
Syncplay Hello, password or room name is sent. Reconnection cannot downgrade the
transport. The pinned certifi bundle supplies application trust roots; machine
trust stores and arbitrary environment variables do not add trust roots.

## Source and license distribution

Retain the complete original sources, applicable license texts and embedded
notices with every development payload. Original grants are verified by hash,
including ZPL modification requirements, historical Python terms, Unicode data
permissions and MIT/BSD attributions. Configuration derivatives carry change
notices and remain separate from originals. The Rust `self_cell` choice is
Apache-2.0; its alternative GPL license is not selected. Other dual MIT/Apache
dependencies retain both original texts. LLVM and Unicode exceptions remain
attached to their respective source.

The certifi PEM remains a separate MPL-2.0 covered file. Include its unmodified
source, its original notice and the complete MPL-2.0 text, and identify its source
revision in the distribution. Do not apply the first-party Apache license to the
bundle. The license-texts submodule supplies complete standard license texts
where the original project provides a reference notice.

BoringSSL is a provenance source for comments already present in cryptography's
PKCS12 implementation; its library is not linked. OpenSSL retains its embedded
AES public-domain notice and BLAKE2 attribution. The original Text::Template
build tool retains its complete alternative licenses. libffi's separately
licensed build and test utilities do not become application runtime code.

Rust/Cargo, Perl, autotools and native C SDKs are developer toolchains. Record
their actual versions and identities for the build. These SDK exceptions do not
permit copying a developer Python runtime, third-party site-packages, system
OpenSSL or system libffi into the application.

The Rust compiler's standard language libraries and compiler support are an
explicit native SDK boundary, like the C compiler's support libraries. Their
exact original vendor components are verified together with the compiler, and
their complete original copyright and license material accompanies the native
output. This boundary covers compiler standard support, including its static
linkage; application crates, Python modules, OpenSSL and libffi are built from
their declared Git sources. No Rust SDK installation is bundled with the app.

## Acceptance

The same aggregate change must build the complete runtime on macOS arm64,
Windows x64 and Linux x64, exercise the native startup contract against the owned
package, initialize real headless Syncplay clients and verify two-client
play/pause/seek through a real upstream TLS server. Include negative TLS cases,
disconnect/reconnect behavior, source and native import audits, relocated
packaging checks and CI. A simulated player verifies the engine boundary only;
actual embedded VLC, native application wiring, signing and release installation
remain separate acceptance gates.

The configured upstream status entry point accepts an explicit user-origin flag. Native observations update actual engine state without entering its existing user-action detection/send path; stock callers retain the original default. The original upstream source is retained unchanged alongside this narrow configuration derivative. The helper session omits stale local heartbeat playstate while its native corrections await acknowledgement; upstream timing and correction decisions remain unchanged.
