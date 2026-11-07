package syncwatch

import java.nio.file.Files
import java.nio.file.Path
import kotlinx.coroutines.*
import syncwatch.library.FileSystemLibrary

internal suspend fun roomPublicationLiveChecks(scope: CoroutineScope) {
    val room = System.getenv("SYNCWATCH_PUBLICATION_ROOM") ?: return
    val caseRoot = Path.of(checkNotNull(System.getenv("SYNCWATCH_PUBLICATION_CASE")))
    val directory = Files.createDirectory(caseRoot.resolve("movies"))
    for (suffix in listOf("A", "B")) Files.writeString(directory.resolve("Linux publication $suffix.mkv"), "authored catalog fixture")
    val rootId = java.util.UUID.randomUUID().toString(); val scanner = FileSystemLibrary(); val controller = RoomController(scope, legacyValidation = true)
    suspend fun waitFor(allowPublicationFailure: Boolean = false, condition: () -> Boolean) = withTimeout(30000) {
        while (!condition()) {
            check(controller.editor.state != "failed" && (allowPublicationFailure || controller.editor.status != "roomCatalogPublishFailed")) { "Native publication failed" }
            delay(20)
        }
    }
    suspend fun waitFile(name: String) = withTimeout(30000) {
        while (!Files.exists(caseRoot.resolve(name))) delay(20)
    }
    fun signal(name: String) { Files.writeString(caseRoot.resolve(name), """{"passed":true}""") }
    try {
        controller.updateLibrary(java.util.UUID.randomUUID(), scanner.scan(directory, rootId, false))
        controller.editor = controller.editor.selectMode("joinRoom").edit("serviceAddress", checkNotNull(System.getenv("SYNCWATCH_ROOM_SERVICE")))
            .edit("username", "Linux normal publisher").edit("roomID", room).edit("invitation", checkNotNull(System.getenv("SYNCWATCH_PUBLICATION_INVITATION")))
        controller.connect()
        val expected = listOf("macOS", "Windows", "Linux").flatMap { listOf("$it publication A", "$it publication B") }.toSet()
        waitFor { controller.catalogView?.items?.map { it.media.title }?.toSet() == expected }
        val original = checkNotNull(controller.catalogView)
        check(original.items.size == 6 && original.items.map { it.media.mediaId }.toSet().size == 6)
        val ownId = original.items.single { it.media.title == "Linux publication A" }.media.mediaId
        signal("phase1.json"); waitFor { Files.exists(caseRoot.resolve("phase2")) }
        Files.delete(directory.resolve("Linux publication B.mkv"))
        controller.updateLibrary(java.util.UUID.randomUUID(), scanner.scan(directory, rootId, false))
        val finalTitles = listOf("macOS", "Windows", "Linux").map { "$it publication A" }.toSet()
        waitFor { controller.catalogView?.items?.let { it.size == 3 && it.map { p -> p.media.title }.toSet() == finalTitles } == true }
        check(checkNotNull(controller.catalogView).items.single { it.media.title == "Linux publication A" }.media.mediaId == ownId && original.items.size == 6)
        signal("phase2.json");
        if (System.getenv("SYNCWATCH_PUBLICATION_RECOVERY") == "1") {
            waitFile("fail-network")
            controller.updateLibrary(java.util.UUID.randomUUID(), scanner.scan(directory, rootId, false))
            waitFor { controller.editor.status == "roomCatalogPublishFailed" }
            check(controller.connected && controller.catalogView?.items?.size == 3)
            signal("failure.json"); waitFile("retry-network")
            val changed = "authored catalog fixture updated after unknown outcome"
            val changedSize = changed.toByteArray(Charsets.UTF_8).size.toLong()
            Files.writeString(directory.resolve("Linux publication A.mkv"), changed)
            controller.updateLibrary(java.util.UUID.randomUUID(), scanner.scan(directory, rootId, false))
            waitFor(allowPublicationFailure = true) { controller.editor.status.isEmpty() && controller.catalogView?.items?.let { it.size == 3 && it.all { item -> item.media.fileSizeBytes == changedSize } } == true }
            check(controller.connected && checkNotNull(controller.catalogView).items.single { it.media.title == "Linux publication A" }.media.mediaId == ownId)
            signal("recovered.json")
        }
 waitFor { Files.exists(caseRoot.resolve("finish")) }
        controller.close(); controller.close(); check(controller.catalogView == null && !controller.connected)
        signal("closed.json"); println("SYNCWATCH_ROOM_PUBLICATION_LIVE_OK all3 catalogs/atomic replacement/removal/close")
    } finally {
        controller.close()
        Files.walk(directory).use { it.sorted(Comparator.reverseOrder()).forEach(Files::delete) }
    }
}
