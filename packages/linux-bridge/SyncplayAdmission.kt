package syncwatch.bridge

import syncwatch.domain.PlaybackScope
import syncwatch.domain.WatchFailure

/** Ephemeral authority for one transport. No credential-bearing data-class representation. */
class SyncplayAdmission(credential: String, clientRunId: String) {
    internal val credential: String = credential
    internal val clientRunId: String = clientRunId
    init {
        if (!credential.matches(Regex("[0-9A-F]{64}"))) throw WatchFailure("malformed")
        PlaybackScope.validateId(clientRunId)
    }
    override fun toString(): String = "[redacted]"
}
