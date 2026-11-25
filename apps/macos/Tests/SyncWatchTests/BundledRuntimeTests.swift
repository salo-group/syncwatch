import Foundation
import Testing

@testable import SyncWatch

@Test func bundledRuntimesPreferResourcesAndStillResolveLegacyPackages() throws {
    let bundle = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".app")
    defer { try? FileManager.default.removeItem(at: bundle) }

    for name in ["Media", "Syncplay"] {
        let legacy = bundle.appendingPathComponent("Contents/Frameworks/\(name)", isDirectory: true)
        let current = bundle.appendingPathComponent("Contents/Resources/\(name)", isDirectory: true)
        let read: ([String]) -> URL = { arguments in
            if name == "Media" { return MediaConfiguration.read(arguments, bundle: bundle).package }
            return HelperConfiguration.read(arguments, bundle: bundle).package
        }
        try FileManager.default.createDirectory(at: legacy, withIntermediateDirectories: true)
        #expect(read([]) == legacy)

        try FileManager.default.createDirectory(at: current, withIntermediateDirectories: true)
        #expect(read([]) == current)

        let override = bundle.appendingPathComponent("owned input")
        let flag = name == "Media" ? "--media-package" : "--helper-package"
        #expect(read([flag, override.path]).path == override.path)
    }
}
