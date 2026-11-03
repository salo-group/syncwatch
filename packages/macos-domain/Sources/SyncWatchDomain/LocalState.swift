import CoreFoundation
import Foundation

/// This initial settings boundary is local only; engine-specific settings are added with adapters.
public struct LocalPreferences: Codable, Equatable, Sendable {
    public var theme = "system"
    public var language = "en"
    public var skipSeconds = 10
    public var previewsEnabled = true
    public var previewCacheMB = 150
    public var cursorHideDelayMs = 2500
    public var defaultMovieVolume: Double?
    public var movieVolume = 1.0
    public var callVolume = 1.0
    public var rememberMovieVolume = true
    public var duckMovieDuringSpeech = false
    public var cameraEnabled = true
    public var microphoneEnabled = true
    // Absent in older state files: retain the selected folder's legacy options until Settings saves them.
    public var scanSubfolders: Bool?
    public var automaticRefresh: Bool?

    public init() {}

    public func validate() throws {
        guard ["system", "dark", "light"].contains(theme), ["en", "ru"].contains(language),
            (1...600).contains(skipSeconds), (50...500).contains(previewCacheMB),
            (500...10_000).contains(cursorHideDelayMs),
            defaultMovieVolume.map { $0.isFinite && (0...1).contains($0) } ?? true,
            movieVolume.isFinite, (0...1).contains(movieVolume),
            callVolume.isFinite, (0...1).contains(callVolume)
        else { throw WatchError.invalidLocalState }
    }
}

public struct ResumePosition: Codable, Equatable, Sendable {
    public let mediaID: String
    public let positionMs: Int64

    enum CodingKeys: String, CodingKey {
        case mediaID = "mediaId"
        case positionMs
    }

    public init(mediaID: String, positionMs: Int64) {
        self.mediaID = mediaID
        self.positionMs = positionMs
    }
}

/// Never persists a process identity, active session, call state, file path, or credential.
public struct LocalState: Codable, Equatable, Sendable {
    public var version = 1
    public var preferences = LocalPreferences()
    public var selectedMediaID: String?
    public var resumePositions: [ResumePosition] = []

    enum CodingKeys: String, CodingKey {
        case version, preferences, resumePositions
        case selectedMediaID = "selectedMediaId"
    }

    public init() {}

    public func validate() throws {
        guard version == 1 else { throw WatchError.unsupportedVersion }
        try preferences.validate()
        if let selectedMediaID { try WatchSession.validateID(selectedMediaID) }
        guard resumePositions.count <= 256, Set(resumePositions.map(\.mediaID)).count == resumePositions.count else {
            throw WatchError.invalidLocalState
        }
        for resume in resumePositions {
            try WatchSession.validateID(resume.mediaID)
            guard (0...WatchSession.maximumPositionMs).contains(resume.positionMs) else {
                throw WatchError.invalidLocalState
            }
        }
    }

    public func resumePosition(for mediaID: String) -> Int64 {
        resumePositions.first { $0.mediaID == mediaID }?.positionMs ?? 0
    }
}

public enum LocalStateRead: Equatable, Sendable {
    case missing
    case loaded(LocalState)
    case invalid
    case unsupportedVersion
}

/// The caller chooses an application-owned file and serializes reads/writes.
public protocol LocalStateStore {
    func read() throws -> LocalStateRead
    func write(_ state: LocalState) throws
}

public struct FileLocalStateStore: LocalStateStore {
    public static let maximumBytes = 65_536
    public let url: URL

    public init(url: URL) { self.url = url }

    public func read() throws -> LocalStateRead {
        let data: Data
        do {
            // Bound reads even if a corrupt file advertises an enormous size.
            let file = try FileHandle(forReadingFrom: url)
            defer { try? file.close() }
            data = try file.read(upToCount: Self.maximumBytes + 1) ?? Data()
        } catch let error as NSError
            where error.domain == NSCocoaErrorDomain
            && [NSFileReadNoSuchFileError, NSFileNoSuchFileError].contains(error.code)
        {
            return .missing
        }
        guard data.count <= Self.maximumBytes else { return .invalid }
        do {
            // Recognize future versions before attempting to decode a newer settings shape.
            guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                let version = object["version"] as? NSNumber,
                Self.isInteger(version), (0...Int64(Int32.max)).contains(version.int64Value)
            else { return .invalid }
            guard version == 1 else { return .unsupportedVersion }
            // Codable accepts some floating-point integer tokens; require the same local format as .NET.
            guard let preferences = object["preferences"] as? [String: Any],
                ["skipSeconds", "previewCacheMB", "cursorHideDelayMs"].allSatisfy({
                    (preferences[$0] as? NSNumber).map(Self.isInteger) ?? false
                }),
                let resumes = object["resumePositions"] as? [[String: Any]],
                resumes.allSatisfy({ ($0["positionMs"] as? NSNumber).map(Self.isInteger) ?? false })
            else { return .invalid }
            let state = try JSONDecoder().decode(LocalState.self, from: data)
            try state.validate()
            return .loaded(state)
        } catch { return .invalid }
    }

    public func write(_ state: LocalState) throws {
        try state.validate()
        guard try read() != .unsupportedVersion else { throw WatchError.unsupportedVersion }
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        let data = try encoder.encode(state)
        guard data.count <= Self.maximumBytes else { throw WatchError.invalidLocalState }
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try data.write(to: url, options: .atomic)
    }

    private static func isInteger(_ number: NSNumber) -> Bool {
        CFGetTypeID(number) != CFBooleanGetTypeID() && !["f", "d"].contains(String(cString: number.objCType))
    }
}
