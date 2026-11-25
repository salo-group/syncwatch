# Building JBR API

The `jbr-api` component builds JetBrains Runtime API 1.9.0 from the pinned `3rdparty/jbr-api` source. This optional-service interface library is a dependency in the planned Compose desktop graph. The component uses the same [verified developer JDK](jvm-annotations.md#developer-compiler-sdk) as JVM annotations, without Gradle, Maven, downloaded library binaries or a JBR runtime.

## Selected source and license scope

[The profile](../tools/jvm/jbr_api.json) records the exact revision and SHA-256 inventory of 28 API Java files, seven build-tool Java files, the JBR Java template, service-getter template, API compatibility baseline and provenance files. All 36 Java files have original Apache-2.0 headers. The profile has no external library dependencies; runtime references use `java.base` and `java.desktop`, and the build tools use `jdk.compiler`. No upstream `NOTICE` exists at this pin.

The source JAR includes the original API and build files, their license
headers, and the `JBR.java` output from the upstream processor. Both JARs
include the full license and notices, upstream README, and source and
compiler inventory.

JBR/OpenJDK runtime implementations, native code, fonts, javadoc generation/assets, publishing tools and upstream manual GUI tests are outside this profile. The API's presence does not select or bundle JetBrains Runtime. On ordinary Temurin, JBR services are unavailable except the upstream font-metrics fallback. Packaged JVMs and the full Compose/native/font graph require their own source, license and distribution review.

## Build and verification

Run from the repository root on Linux:

```sh
git submodule update --init --checkout --depth=1 -- 3rdparty/jbr-api
python3 tools/jvm/jdk.py --provision
python3 tools/dependencies/build.py build --component jbr-api --platform linux-x64 --cold
python3 tools/dependencies/build.py build --component jbr-api --platform linux-x64
python3 -m unittest discover -s tools/jvm/tests -v
```

This component belongs to the Kotlin/Linux application and is built and probed only on Linux. macOS/Swift and Windows/C# do not provision a developer JVM for it. SDK provisioning is an explicit online step. Compilation and artifact verification use the dependency builder's offline environment, isolated class/source/processor paths, UTF-8 and no debug information. `JAVA_TOOL_OPTIONS`, `JDK_JAVA_OPTIONS`, `JDK_JAVAC_OPTIONS`, `_JAVA_OPTIONS` and ambient classpaths are removed from compiler and probe environments.

The upstream processor generates `JBR.java` and compares the API with the
pinned 1.9.0 baseline. The build requires version `1.9.0` and an empty
change report. The upstream compatibility plugin compiles the Java 8
surface and removes duplicate Java 9 classes. The Java 9 module declares
version `1.9.0`. The build uses the pinned compiler and sorts the input
paths. The packager sorts ZIP entries and sets fixed timestamps and modes.
The profile pins the generated source and both JAR digests.

Cold builds and cache reads verify the payload, both JAR digests, and
all original and generated source entries. A consumer compiles service,
callback, and extension calls for Java 8. Runtime tests cover classpath,
named-module, and Java 8 overlay loading on Temurin. They exercise
service getters, extensions, and the font fallback. `jdeps` checks
the SDK module dependencies. Temurin 21 runs all three headless tests,
including the Java 8 overlay. Actual JBR services and Compose windows
require separate native acceptance.

## Artifacts

The payload under `artifacts/dependencies/jbr-api/PLATFORM/payload`
contains the API JAR, its source JAR, `THIRD-PARTY-NOTICES.txt`, and
`source-inventory.json`. It includes the original license and README
under `notices/jbr-api`. Preserve these materials with the API.
Review source, compiler, and packaging changes before updating the
output digests.
