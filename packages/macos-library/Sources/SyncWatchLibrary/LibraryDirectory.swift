import Darwin
import Foundation

/// Lists and inspects descendants relative to an open directory.
final class LibraryDirectory {
    private let stream: UnsafeMutablePointer<DIR>

    init(root: URL) throws {
        let descriptor = root.path.withCString { path in
            open(path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        }
        stream = try Self.takeStream(descriptor)
    }

    init(parent: LibraryDirectory, name: String) throws {
        stream = try Self.takeStream(
            openat(dirfd(parent.stream), name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC))
    }

    deinit { closedir(stream) }

    private static func takeStream(_ descriptor: Int32) throws -> UnsafeMutablePointer<DIR> {
        if descriptor < 0 {
            switch errno {
            case ENOENT, ENOTDIR: throw CocoaError(.fileReadNoSuchFile)
            case EACCES, EPERM: throw CocoaError(.fileReadNoPermission)
            default: throw CocoaError(.fileReadUnknown)
            }
        }
        guard let stream = fdopendir(descriptor) else {
            close(descriptor)
            throw CocoaError(.fileReadUnknown)
        }
        return stream
    }

    func names() throws -> [String] {
        var names: [String] = []
        while true {
            try Task.checkCancellation()
            errno = 0
            guard let entry = readdir(stream) else {
                if errno != 0 { throw CocoaError(.fileReadUnknown) }
                return names
            }
            let name = withUnsafePointer(to: &entry.pointee.d_name) {
                $0.withMemoryRebound(to: CChar.self, capacity: Int(entry.pointee.d_namlen) + 1) {
                    String(validatingCString: $0)
                }
            }
            guard let name else { throw CocoaError(.fileReadInapplicableStringEncoding) }
            if name != "." && name != ".." { names.append(name) }
        }
    }

    func attributes(_ name: String) throws -> stat? {
        var facts = stat()
        if fstatat(dirfd(stream), name, &facts, AT_SYMLINK_NOFOLLOW) == 0 { return facts }
        if errno == ENOENT { return nil }
        throw CocoaError(.fileReadUnknown)
    }
}
