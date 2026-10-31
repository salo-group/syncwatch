import SwiftUI

public enum SettingsVisualState: String, CaseIterable, Sendable {
    case editing, savedNotice, invalidConfiguration
}

/// Local presentation draft. A successful persistence acknowledgement is required for saved feedback.
public struct SettingsEditor: Sendable {
    public private(set) var values: [String: String]
    public let editableFields: Set<String>
    public let canSave: Bool
    public private(set) var state: SettingsVisualState
    public private(set) var message = ""
    private var original: [String: String]

    public init(
        values: [String: String] = [:], editableFields: Set<String> = [], canSave: Bool = false,
        state: SettingsVisualState = .editing
    ) {
        self.values = SyncWatchSettings.defaults.merging(values) { _, value in value }
        original = self.values
        self.editableFields = editableFields
        self.canSave = canSave
        self.state = state
        if state == .invalidConfiguration { message = "invalidConfiguration" }
    }

    public mutating func edit(_ id: String, value: String) {
        guard editableFields.contains(id),
            SyncWatchSettings.fields.contains(where: { $0.id == id && $0.kind != "folder" })
        else { return }
        values[id] = value
        state = .editing
        message = ""
    }

    public var isValid: Bool {
        guard Set(values.keys) == Set(SyncWatchSettings.defaults.keys),
            editableFields.isSubset(of: Set(SyncWatchSettings.defaults.keys))
        else { return false }
        return SyncWatchSettings.fields.allSatisfy { field in
            guard let value = values[field.id], value.utf16.count <= field.maxLength,
                !value.unicodeScalars.contains(where: { $0.value < 32 || (127...159).contains($0.value) })
            else { return false }
            if field.kind == "choice" { return field.options.contains(value) }
            if field.kind == "boolean" { return ["true", "false"].contains(value) }
            return true
        }
    }

    @discardableResult public mutating func save(using persist: ([String: String]) -> Bool) -> Bool {
        guard canSave else { return false }
        guard isValid else {
            state = .invalidConfiguration
            message = "invalidConfiguration"
            return false
        }
        guard persist(values) else {
            state = .invalidConfiguration
            message = "saveFailed"
            return false
        }
        original = values
        state = .savedNotice
        message = "changesSaved"
        return true
    }

    public mutating func cancel() {
        values = original
        state = .editing
        message = ""
    }
}

public struct SettingsDialog: View {
    @Binding private var editor: SettingsEditor
    public var language: String
    public var onSave: ([String: String]) -> Bool
    public var onClose: () -> Void
    public var folderPath: String
    public var canChooseFolder: Bool
    public var onChooseFolder: () -> Void
    @State private var section: String
    public init(
        editor: Binding<SettingsEditor>, language: String = "en", section: String = "interface",
        folderPath: String = "", canChooseFolder: Bool = false, onChooseFolder: @escaping () -> Void = {},
        onSave: @escaping ([String: String]) -> Bool = { _ in false }, onClose: @escaping () -> Void = {}
    ) {
        _editor = editor
        self.language = language
        self.onSave = onSave
        self.onClose = onClose
        self.folderPath = folderPath
        self.canChooseFolder = canChooseFolder
        self.onChooseFolder = onChooseFolder
        _section = State(initialValue: SyncWatchSettings.sections.contains(section) ? section : "interface")
    }
    public var body: some View {
        DesignSurface(
            "Settings",
            context: DesignBindings.settings(
                editor, section: section, folderPath: folderPath, canChooseFolder: canChooseFolder), language: language
        ) {
            event in
            switch event.action {
            case "selectSection":
                if let value = event.value, SyncWatchSettings.sections.contains(value) { section = value }
            case "editField": if let id = event.payload, let value = event.value { editor.edit(id, value: value) }
            case "chooseFolder": if canChooseFolder && editor.canSave { onChooseFolder() }
            case "save": editor.save(using: onSave)
            case "close", "cancel":
                editor.cancel()
                onClose()
            default: break
            }
        }
    }
}
