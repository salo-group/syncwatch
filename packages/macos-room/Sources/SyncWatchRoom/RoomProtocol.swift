import CoreFoundation
import Foundation

public struct RoomIdentity: Sendable, Equatable {
    public let clientRunID: String
    public let syncplayUsername: String
    public static func == (lhs: Self, rhs: Self) -> Bool {
        lhs.clientRunID == rhs.clientRunID && lhs.syncplayUsername.utf8.elementsEqual(rhs.syncplayUsername.utf8)
    }
    public init(clientRunID: String, syncplayUsername: String) throws {
        try RoomWire.id(clientRunID)
        try RoomWire.text(syncplayUsername, maximum: 64)
        self.clientRunID = clientRunID
        self.syncplayUsername = syncplayUsername
    }
}
public struct RoomMember: Sendable, Equatable {
    public let memberID: String
    public let identity: RoomIdentity
}
public struct RoomConnection: Sendable, Equatable {
    public let host: String
    public let port: Int
    public let room: String
    public init(host: String, port: Int, room: String) throws {
        try RoomWire.text(host, maximum: 253)
        try RoomWire.text(room, maximum: 64)
        guard (1...65535).contains(port), host.range(of: "\\A[A-Za-z0-9.:-]+\\z", options: .regularExpression) != nil
        else { throw RoomProtocolFailure.malformed }
        self.host = host
        self.port = port
        self.room = room
    }
}
public struct RoomSnapshot: Sendable, Equatable {
    public let epoch: String
    public let sequence: UInt64
    public let watchGeneration: UInt64
    public let watchStatus: String
    public let mediaID: String?
    public let members: [RoomMember]
    public let meetURI: String?
}
public struct RoomBootstrap: Sendable, Equatable {
    public let snapshot: RoomSnapshot
    public let connection: RoomConnection
    public let catalogCursor: String
    public let missingMembers: [String]
    public static func initialEvent(_ bytes: Data, admission: RoomAdmission) throws -> Self {
        let value = try RoomWire.object(bytes)
        try RoomWire.keys(value, ["v", "type", "data"])
        guard try RoomWire.integer(value["v"], maximum: 1) == 1, value["type"] as? String == "room.bootstrap"
        else { throw RoomProtocolFailure.malformed }
        let result = try RoomWire.bootstrap(value["data"])
        let expected = admission.bootstrap
        guard result.snapshot.epoch == expected.snapshot.epoch, result.connection == expected.connection,
            result.snapshot.members.first(where: { $0.memberID == admission.memberID })
                == expected.snapshot.members.first(where: { $0.memberID == admission.memberID })
        else { throw RoomProtocolFailure.malformed }
        return result
    }
    public static func decode(_ bytes: Data) throws -> Self { try RoomWire.bootstrap(RoomWire.object(bytes)) }
}
public struct RoomAdmission: Sendable {
    public let roomID: String
    public let memberID: String
    public let token: RoomSecret?
    var accountScope: UUID? = nil
    public var isAccountBacked: Bool { accountScope != nil }
    public let invitation: RoomSecret?
    public let expiresAt: Date
    public let bootstrap: RoomBootstrap
    public static func decode(_ bytes: Data, identity: RoomIdentity, owner: Bool) throws -> Self {
        let value = try RoomWire.object(bytes)
        try RoomWire.keys(value, ["roomId", "memberId", "token", "invitation", "expiresAt", "bootstrap"])
        let room = try RoomWire.string(value["roomId"])
        let member = try RoomWire.string(value["memberId"])
        try RoomWire.id(room)
        try RoomWire.id(member)
        let bootstrap = try RoomWire.bootstrap(value["bootstrap"])
        guard bootstrap.snapshot.members.first(where: { $0.memberID == member })?.identity == identity else {
            throw RoomProtocolFailure.malformed
        }
        let invitation = try RoomWire.optionalString(value["invitation"])
        guard (invitation != nil) == owner else { throw RoomProtocolFailure.malformed }
        let time = try RoomWire.string(value["expiresAt"])
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let fractional = formatter.date(from: time)
        formatter.formatOptions = [.withInternetDateTime]
        guard let expires = fractional ?? formatter.date(from: time) else { throw RoomProtocolFailure.malformed }
        return Self(
            roomID: room, memberID: member, token: try RoomSecret(RoomWire.string(value["token"])),
            invitation: try invitation.map(RoomSecret.init), expiresAt: expires, bootstrap: bootstrap)
    }
}

enum RoomWire {
    static let maximumBytes = 65_536
    static let maximumCounter: UInt64 = 9_007_199_254_740_991
    static func id(_ value: String) throws {
        guard value.range(of: "\\A[A-Za-z0-9][A-Za-z0-9._-]{0,63}\\z", options: .regularExpression) != nil
        else { throw RoomProtocolFailure.malformed }
    }
    static func text(_ value: String, maximum: Int) throws {
        guard (1...maximum).contains(value.unicodeScalars.count),
            !value.unicodeScalars.contains(where: { $0.value < 32 || $0.value == 127 })
        else { throw RoomProtocolFailure.malformed }
    }
    static func string(_ value: Any?) throws -> String {
        guard let value = value as? String else { throw RoomProtocolFailure.malformed }
        return value
    }
    static func optionalString(_ value: Any?) throws -> String? {
        if value is NSNull { return nil }
        return try string(value)
    }
    static func keys(_ value: [String: Any], _ names: Set<String>) throws {
        guard Set(value.keys) == names else { throw RoomProtocolFailure.malformed }
    }
    static func integer(_ value: Any?, maximum: UInt64 = maximumCounter) throws -> UInt64 {
        guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
            number.doubleValue >= 0, number.doubleValue <= Double(maximum),
            number.doubleValue.rounded(.towardZero) == number.doubleValue
        else { throw RoomProtocolFailure.malformed }
        return number.uint64Value
    }
    static func dictionary(_ value: Any?) throws -> [String: Any] {
        guard let value = value as? [String: Any] else { throw RoomProtocolFailure.malformed }
        return value
    }
    static func bootstrap(_ input: Any?) throws -> RoomBootstrap {
        let value = try dictionary(input)
        try keys(value, ["snapshot", "connection", "catalogCursor", "missingMembers"])
        let snapshot = try snapshot(value["snapshot"])
        let connection = try dictionary(value["connection"])
        try keys(connection, ["host", "port", "room", "requireTls"])
        guard let tls = connection["requireTls"] as? NSNumber, CFGetTypeID(tls) == CFBooleanGetTypeID(), tls.boolValue,
            let missing = value["missingMembers"] as? [String], missing.count <= 64,
            Set(missing).count == missing.count,
            Set(missing).isSubset(of: Set(snapshot.members.map(\.memberID)))
        else { throw RoomProtocolFailure.malformed }
        let cursor = try string(value["catalogCursor"])
        try text(cursor, maximum: 128)
        return RoomBootstrap(
            snapshot: snapshot,
            connection: try RoomConnection(
                host: string(connection["host"]), port: Int(integer(connection["port"], maximum: 65535)),
                room: string(connection["room"])),
            catalogCursor: cursor, missingMembers: missing)
    }
    static func snapshot(_ input: Any?) throws -> RoomSnapshot {
        let value = try dictionary(input)
        try keys(value, ["v", "roomEpoch", "sequence", "watchGeneration", "watch", "members", "meetUri"])
        guard try integer(value["v"], maximum: 1) == 1, let members = value["members"] as? [[String: Any]],
            (1...64).contains(members.count)
        else { throw RoomProtocolFailure.malformed }
        let epoch = try string(value["roomEpoch"])
        try id(epoch)
        let bindings: [RoomMember] = try members.map {
            try keys($0, ["memberId", "clientRunId", "syncplayUsername"])
            let member = try string($0["memberId"])
            try id(member)
            return RoomMember(
                memberID: member,
                identity: try RoomIdentity(
                    clientRunID: string($0["clientRunId"]), syncplayUsername: string($0["syncplayUsername"])))
        }
        guard Set(bindings.map(\.memberID)).count == bindings.count,
            Set(bindings.map { Data($0.identity.syncplayUsername.utf8) }).count == bindings.count
        else { throw RoomProtocolFailure.malformed }
        let watch = try dictionary(value["watch"])
        try keys(watch, ["status", "mediaId"])
        let status = try string(watch["status"])
        let media = try optionalString(watch["mediaId"])
        guard ["idle", "selected", "active", "ended"].contains(status), (media == nil) == (status == "idle")
        else { throw RoomProtocolFailure.malformed }
        if let media { try id(media) }
        let meet = try optionalString(value["meetUri"])
        try validateMeetURI(meet)
        return RoomSnapshot(
            epoch: epoch, sequence: try integer(value["sequence"]),
            watchGeneration: try integer(value["watchGeneration"]),
            watchStatus: status, mediaID: media, members: bindings, meetURI: meet)
    }
    static func validateMeetURI(_ value: String?) throws {
        guard
            value.map({
                $0.range(
                    of: "\\Ahttps://meet\\.google\\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}\\z", options: .regularExpression)
                    != nil
            }) ?? true
        else { throw RoomProtocolFailure.malformed }
    }
    static func encode(_ value: Any) throws -> Data {
        guard JSONSerialization.isValidJSONObject(value) else { throw RoomProtocolFailure.malformed }
        let bytes = try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
        guard bytes.count <= maximumBytes else { throw RoomProtocolFailure.frameTooLarge }
        return bytes
    }
    static func object(_ data: Data) throws -> [String: Any] { try dictionary(value(data)) }
    static func value(_ data: Data) throws -> Any {
        guard data.count <= maximumBytes else { throw RoomProtocolFailure.frameTooLarge }
        guard String(data: data, encoding: .utf8) != nil else { throw RoomProtocolFailure.malformed }
        // Reject duplicate names before Foundation collapses them. Room counters have integer syntax.
        let bytes = Array(data)
        var stack: [Set<String>] = []
        var index = 0
        while index < bytes.count {
            let byte = bytes[index]
            if byte == 123 || byte == 91 {
                stack.append([])
                guard stack.count <= 32 else { throw RoomProtocolFailure.malformed }
            } else if byte == 125 || byte == 93 {
                guard !stack.isEmpty else { throw RoomProtocolFailure.malformed }
                stack.removeLast()
            } else if byte == 34 {
                let start = index
                index += 1
                while index < bytes.count && bytes[index] != 34 {
                    if bytes[index] == 92 { index += 1 }
                    index += 1
                }
                guard index < bytes.count else { throw RoomProtocolFailure.malformed }
                var next = index + 1
                while next < bytes.count && [9, 10, 13, 32].contains(bytes[next]) { next += 1 }
                if next < bytes.count && bytes[next] == 58 {
                    guard !stack.isEmpty,
                        let key = try JSONSerialization.jsonObject(
                            with: Data(bytes[start...index]), options: [.fragmentsAllowed]) as? String,
                        stack[stack.count - 1].insert(key).inserted
                    else { throw RoomProtocolFailure.malformed }
                }
            } else if byte == 45 || (48...57).contains(byte) {
                var end = index + 1
                while end < bytes.count && ![9, 10, 13, 32, 44, 93, 125].contains(bytes[end]) { end += 1 }
                let token = String(decoding: bytes[index..<end], as: UTF8.self)
                guard token.range(of: "\\A(0|[1-9][0-9]*)\\z", options: .regularExpression) != nil else {
                    throw RoomProtocolFailure.malformed
                }
                index = end - 1
            }
            index += 1
        }
        guard stack.isEmpty else { throw RoomProtocolFailure.malformed }
        do { return try JSONSerialization.jsonObject(with: data) } catch { throw RoomProtocolFailure.malformed }
    }
}
