package syncwatch.library

import java.nio.file.Files
import java.nio.file.Path
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.cancel
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import syncwatch.ipc.Json

fun main(arguments: Array<String>) = runBlocking {
    libraryFileIndexChecks(Path.of(arguments.single()))
    libraryCompanionScanChecks(Path.of(arguments.single()))
    libraryChangeMonitorChecks(Path.of(arguments.single()))
    mediaMetadataChecks()
    fileContentHasherChecks(Path.of(arguments.single()))
    @Suppress("UNCHECKED_CAST")
    val corpus = Json.parse(Files.readString(Path.of(arguments.single()))) as Map<String, Any?>
    val id = corpus["rootId"] as String
    @Suppress("UNCHECKED_CAST")
    val source = corpus["files"] as List<Map<String, Any?>>
    @Suppress("UNCHECKED_CAST")
    val expected = corpus["expected"] as List<Map<String, Any?>>
    val root = Files.createTempDirectory("syncwatch-library-")
    val alias = Files.createTempDirectory("syncwatch-library-alias-")
    try {
        for (file in source) {
            val path = root.resolve(file["path"] as String)
            Files.createDirectories(path.parent)
            Files.writeString(path, file["content"] as String)
        }
        Files.createSymbolicLink(root.resolve("link.mp4"), root.resolve("root.mkv"))
        Files.createSymbolicLink(root.resolve("nested/cycle"), root)
        val scanner = FileSystemLibrary()
        val result = scanner.scan(root, id, true)
        check(result.map { it.relativePath } == expected.map { it["relativePath"] })
        check(result.map { it.id } == expected.map { it["id"] })
        check(result.map { it.title } == expected.map { it["title"] })
        check(result.map { it.sizeBytes } == expected.map { (it["sizeBytes"] as Number).toLong() })
        check(scanner.scan(root, id, false).map { it.relativePath } == expected.map { it["relativePath"] as String }.filter { '/' !in it })
        check(result.map { it.id }.intersect(scanner.scan(root, corpus["otherRootId"] as String, true).map { it.id }.toSet()).isEmpty())
        launch {
            currentCoroutineContext().cancel()
            try { scanner.scan(root, id, true); error("cancelled-snapshot") } catch (_: CancellationException) { }
        }.join()
        check(runCatching { scanner.scan(root.resolve("absent"), id, true) }.isFailure)
        val linkRoot = Files.createSymbolicLink(alias.resolve("root"), root)
        check(runCatching { scanner.scan(linkRoot, id, true) }.isFailure)
        check(runCatching { scanner.scan(root, "invalid", true) }.isFailure)
        libraryAccessChecks(Path.of(arguments.single()), root)
        println("SYNCWATCH_LIBRARY_OK entries=${result.size} symlinkChecks=true")
    } finally {
        Files.deleteIfExists(alias.resolve("root"))
        Files.delete(alias)
        Files.walk(root).use { paths -> paths.sorted(Comparator.reverseOrder()).forEach { Files.delete(it) } }
    }
}
