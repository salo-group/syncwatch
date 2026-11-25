# Licenses and distribution

SyncWatch's first-party source and shared design data use [Apache License 2.0](../LICENSE). External libraries retain their own licenses; the first-party license does not replace them.

## Design tools

The native Node design profile contains one Apache-2.0 package (Style Dictionary), nine MIT packages, and five BlueOak-1.0.0 packages. These permissive licenses allow use, modification, and distribution with their respective notice conditions. Preserve the full license texts, original copyright/attribution notices, and modification notices when distributing the tool or its source.

Every built runtime includes:

- The first-party Apache license in `LICENSE`.
- A full license and any upstream `NOTICE` inside each package directory.
- Each package's unmodified original metadata in `upstream-package.json`.
- `THIRD-PARTY-NOTICES.txt`, collecting full notice texts and embedded-source attributions.
- `license-inventory.json`, identifying the source revisions, license texts, and modified files.
- Prominent SyncWatch modification notices in changed JavaScript and package metadata.

The [source inventory](../tools/design/sources.json), [reviewed license text digests](../tools/design/licenses.json), and [component registry](../tools/dependencies/components.json) specify the exact distributed profile. Embedded Chalk sources share the retained Sindre Sorhus MIT notice. Color.js's HSLuv/HPLuv copyright and permission headers are retained verbatim; the ColorAide notice identifies the original HCT implementation's author and terms. Supplemental notice provenance is recorded with an upstream reference and digest. No copyright holder or year is synthesized from a username or commit date.

The build verifies license text digests, original license declarations, embedded-source attribution, and modification notices. The distribution check also runs on a restored design-tool cache:

```sh
python3 tools/design/check.py
```

The [build guide](design-generator.md) describes the supported runtime profile. Node and Python are developer toolchains; they are not needed to display generated native resources.

## JVM annotations

The source-built [JVM annotations profile](jvm-annotations.md) uses Apache-2.0 with the full upstream license and original notices in both JARs and the payload. All 32 selected Java sources remain unchanged, including two files governed by the repository-wide declaration without individual headers. The binary JAR links only to Java SDK interfaces; no external runtime library or annotation processor is included. Its Temurin compiler SDK retains vendor legal files separately and is not included in the library or application artifacts.

The source-built JBR API profile uses Apache-2.0. It includes the
original API and generator files, their full license and headers, and
the generated Java source. The binary contains service interfaces and
the upstream Java fallback. Preserve the source inventory and notices
when distributing the API. [JBR API profile](jbr-api.md).

The dependency verifier checks notices, the original source inventory, and source/compiler correspondence on restored caches as well as cold builds. These checks cover the declared annotations and JBR API profiles. The Compose/native graph and any packaged JVM runtime require separate source and distribution reviews.

The [Kotlin stdlib source package](kotlin-stdlib-sources.md) retains Apache-2.0, GWT/Guava, BSD-3-Clause ThreeTenBP and BSL-1.0 Boost notices with all selected original sources. Its two Unicode 13 data files retain the full pinned Unicode-DFS-2016 notice and original data attribution. Publication digests and common/JVM titlecase correspondence are verified offline. This source-only package contains no compiler, binary stdlib, native library, font or JVM; it does not clear those future distributions.

## Native hash library

The [native hash profile](native-hashing.md) retains all 48 selected original files and complete notices. Generated HACL hash C/header sources use MIT with their original INRIA, CMU, Microsoft and HACL Contributors notices; HACL's root Apache-2.0 and full distribution MIT text are both retained. Original KaRaMeL runtime headers use Apache-2.0. The cited Arduino endian origin retains the full 2015 Southern Storm Software MIT notice. CPython reference files retain the full Python-2.0.1 and historical PSF/BeOpen/CNRI/CWI license chain. Separate generated namespace configuration and first-party build/probe code do not alter these original files. Preserve all licenses, attributions and source correspondence; future vendor modifications require appropriate prominent change notices. This scope does not clear the full interpreter or a TLS provider.

## Other components

The [embedded interpreter profile](embedded-python.md) retains CPython's complete PSF/BeOpen/CNRI/CWI license chain and the complete selected embedded-source grants. These include Lucent's dtoa permission/disclaimer and existing modification history, David Gottner's getopt grant, Google's dynamic-annotation redistribution/nonendorsement terms, Don Owens's BSD hashtable grant, the SipHash MIT text, and Secret Labs/Fredrik Lundh Unicode and SRE notices. SRE's reference to Python 1.6 is accompanied by that complete historical license. Full original source files and the complete upstream license appendix preserve platform-specific socket/select and other embedded attributions. Zlib retains its original permissive license and source notices. The interpreter source is compatible with the first-party Apache-2.0 embedding arrangement; it retains its own terms. Preserve all required texts, copyright notices, original source correspondence and the separate selection/configuration change record.

The selected build excludes optional OpenSSL, HACL hashing, expat, mpdecimal, libffi/ctypes, SQLite, readline, Tk, bzip2 and lzma modules. Their inactive build metadata is not clearance for a future enabled binary. No platform SDK or Microsoft runtime DLL is redistributed; the installed platform runtime remains a prerequisite. TLS/CA and the complete Syncplay runtime require their own enabled-graph and final distribution review.

The interpreter's Unicode type tables retain the complete Unicode-DFS-2016 grant, original Unicode 15.0 inputs and CPython generator notice from the [Unicode source profile](python-unicode.md). The table and its data revision match that independently verified profile; no new Unicode data or normalization/name module is adopted.

The [dependency architecture](architecture/dependencies.md) records the other pinned sources. Syncplay uses Apache-2.0; fastjsonschema uses BSD-3-Clause and retains its license in the built validator package. The native shells and metadata service use the declared platform SDKs/frameworks without registry library packages.

VLC contains code under multiple licenses. The libVLC API is LGPL-2.1-or-later; the complete player, plugins, codecs, and contrib libraries require a distribution inventory for the selected build. Preserve applicable licenses and corresponding source, and support the replacement/relinking rights required by the selected LGPL distribution method. A license entry for the top-level VLC repository is not clearance for every possible module combination. The media component is not yet a registered distributable build.

The planned Linux UI uses Kotlin/JVM and Compose Multiplatform Desktop. JetBrains-owned [Compose code](https://github.com/JetBrains/compose-multiplatform/blob/master/license/README.md), [Kotlin code](https://github.com/JetBrains/kotlin/blob/master/license/README.md), and [Skiko](https://github.com/JetBrains/skiko/blob/master/LICENSE) use Apache-2.0, with separate third-party notices where applicable. [Skia](https://github.com/google/skia/blob/main/LICENSE), its enabled dependencies, bundled fonts, and any packaged Java runtime retain their own terms. The owned [Compose runtime](compose-runtime.md) and [composition state](compose-state.md) profiles retain exact selected original sources, complete grants and headers, resources and build-profile notices. The latter adds Apache-2.0 AndroidX state/lifecycle, serialization and Swing dispatcher sources, including explicitly inventoried repository-licensed declarations without individual headers. Its matching named SDK plugins are compiler-process inputs only. The complete UI/native/font graph and packaged JVM distribution review remain required under the [source dependency policy](architecture/dependencies.md).

## License texts

- [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0).
- [MIT License](https://spdx.org/licenses/MIT.html).
- [Blue Oak Model License 1.0.0](https://blueoakcouncil.org/license/1.0.0).
- [BSD 3-Clause](https://spdx.org/licenses/BSD-3-Clause.html).
- [Pinned libVLC license](https://github.com/videolan/vlc/blob/6de05adcbaf2e8b85fe86aad4169393098628119/COPYING.LIB).
