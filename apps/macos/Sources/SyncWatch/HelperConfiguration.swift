import Foundation
import SyncWatchBridge

/// Local installation configuration. Room metadata never supplies executable or runtime paths.
struct HelperConfiguration: Sendable {
    let package: URL

    static func read(_ arguments: [String], bundle: URL = Bundle.main.bundleURL) -> Self {
        let value = arguments.firstIndex(of: "--helper-package").flatMap {
            $0 + 1 < arguments.count ? arguments[$0 + 1] : nil
        }
        return Self(
            package: value.map { URL(filePath: $0).standardizedFileURL }
                ?? BundledRuntime.package("Syncplay", bundle: bundle))
    }

    func verify() async throws -> HelperRuntime {
        try Task.checkCancellation()
        let package = package
        let verification = Task.detached(priority: .utility) { try HelperRuntime(package: package) }
        let runtime = try await withTaskCancellationHandler(
            operation: { try await verification.value },
            onCancel: { verification.cancel() })
        try Task.checkCancellation()
        return runtime
    }
}
