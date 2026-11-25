import Foundation

enum BundledRuntime {
    static func package(_ name: String, bundle: URL) -> URL {
        let resource = bundle.appendingPathComponent("Contents/Resources/\(name)", isDirectory: true)
        let legacy = bundle.appendingPathComponent("Contents/Frameworks/\(name)", isDirectory: true)
        if !FileManager.default.fileExists(atPath: resource.path), FileManager.default.fileExists(atPath: legacy.path) {
            return legacy
        }
        return resource
    }
}
