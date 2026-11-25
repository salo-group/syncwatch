import Foundation
import SyncWatchPlayback

/// Application-local runtime location, shared by Library and explicit playback launches.
struct MediaConfiguration: Sendable {
    let package: URL

    static func read(_ arguments: [String], bundle: URL = Bundle.main.bundleURL) -> Self {
        let index = arguments.firstIndex(of: "--media-package")
        let value = index.flatMap { $0 + 1 < arguments.count ? arguments[$0 + 1] : nil }
        return Self(
            package: value.map { URL(filePath: $0).standardizedFileURL }
                ?? BundledRuntime.package("Media", bundle: bundle))
    }

    func verify() throws { _ = try MediaRuntime(package: package) }
}
