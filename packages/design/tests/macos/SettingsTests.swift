import AppKit
import SwiftUI
import Testing

@testable import SyncWatchDesign

@Test func settingsDraftRequiresValidCapabilitiesAndPersistenceAcknowledgement() {
    var editor = SettingsEditor(editableFields: ["theme", "language"], canSave: true)
    editor.edit("folder", value: "ignored")
    editor.edit("unregistered", value: "ignored")
    #expect(editor.values["folder"] == "" && editor.values["unregistered"] == nil)
    editor.edit("theme", value: "invalid")
    var writes = 0
    #expect(
        !editor.save { _ in
            writes += 1
            return true
        })
    #expect(writes == 0 && editor.state == .invalidConfiguration)
    editor.cancel()
    #expect(editor.values["theme"] == "system")
    editor.edit("theme", value: "dark")
    #expect(!editor.save { _ in false })
    #expect(editor.state == .invalidConfiguration && editor.message == "saveFailed")
    #expect(
        editor.save { values in
            writes += 1
            return values["theme"] == "dark"
        })
    #expect(editor.state == .savedNotice && writes == 1)
    editor.edit("theme", value: "light")
    editor.cancel()
    #expect(editor.values["theme"] == "dark")
    var readonly = SettingsEditor()
    #expect(
        !readonly.save { _ in
            writes += 1
            return true
        })
    #expect(writes == 1)
    var controls = SettingsEditor(editableFields: ["username"], canSave: true)
    controls.edit("username", value: "bad\u{7F}")
    #expect(!controls.isValid)
    let invalid = SettingsEditor(values: ["unexpected": "value"], canSave: true)
    #expect(!invalid.isValid)
    #expect(SettingsVisualState.allCases.map(\.rawValue) == SyncWatchSettings.states)
}

@Test func folderSelectionIsAnObservedCapabilityOutsideTheSettingsDraft() throws {
    let path = "/Movies/Кино " + String(repeating: "long-path/", count: 70)
    for canSave in [false, true] {
        for canChoose in [false, true] {
            var editor = SettingsEditor(editableFields: ["theme", "folder"], canSave: canSave)
            editor.edit("theme", value: "dark")
            editor.edit("folder", value: "ungranted")
            let context = DesignBindings.settings(
                editor, section: "library", folderPath: path, canChooseFolder: canChoose)
            let folder = try #require(context.collections["fields"]?.first { $0.values["id"] == "folder" })
            #expect(folder.values["kind"] == "folder" && folder.values["value"] == path)
            #expect(folder.values["canChooseFolder"] == String(canSave && canChoose))
            #expect(editor.isValid && editor.values["theme"] == "dark" && editor.values["folder"] == "")
            if canSave { #expect(editor.save { $0["folder"] == "" && $0["theme"] == "dark" }) }
        }
    }
}

@MainActor @Test func renderAllSettingsSectionsStatesThemesAndLanguages() throws {
    NSApplication.shared.setActivationPolicy(.prohibited)
    let output = ProcessInfo.processInfo.environment["SYNCWATCH_DESIGN_SCREENSHOTS"].map {
        URL(fileURLWithPath: $0, isDirectory: true)
    }
    if let output { try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true) }
    var referenceColors: [String: NSColor] = [:]
    for section in SyncWatchSettings.sections {
        for state in SettingsVisualState.allCases {
            for (name, scheme) in [("dark", ColorScheme.dark), ("light", ColorScheme.light)] {
                for language in ["en", "ru"] {
                    let editor = SettingsEditor(
                        editableFields: Set(SyncWatchSettings.defaults.keys), canSave: true, state: state)
                    let view = SettingsDialog(editor: .constant(editor), language: language, section: section)
                        .environment(\.colorScheme, scheme)
                    let frame = NSRect(x: 0, y: 0, width: 680, height: 420)
                    let hosting = NSHostingView(rootView: view)
                    hosting.frame = frame
                    let window = NSWindow(
                        contentRect: frame, styleMask: [.borderless], backing: .buffered, defer: false)
                    window.isReleasedWhenClosed = false
                    window.contentView = hosting
                    hosting.layoutSubtreeIfNeeded()
                    let bitmap = try #require(hosting.bitmapImageRepForCachingDisplay(in: hosting.bounds))
                    hosting.cacheDisplay(in: hosting.bounds, to: bitmap)
                    #expect(!window.isVisible)
                    if section == "interface" {
                        let scale = CGFloat(bitmap.pixelsWide) / frame.width
                        let actual = try #require(
                            bitmap.colorAt(x: Int(430 * scale), y: Int(135 * scale))?.usingColorSpace(.sRGB))
                        if referenceColors[name] == nil {
                            referenceColors[name] = try renderedReferenceColor(SyncWatchTheme.resolve(scheme).inputFill)
                        }
                        let expected = try #require(referenceColors[name])
                        #expect(abs(actual.redComponent - expected.redComponent) <= 1.0 / 255 + 0.0001)
                        #expect(abs(actual.greenComponent - expected.greenComponent) <= 1.0 / 255 + 0.0001)
                        #expect(abs(actual.blueComponent - expected.blueComponent) <= 1.0 / 255 + 0.0001)
                    }
                    #expect(try #require(bitmap.colorAt(x: 40, y: 40)).alphaComponent > 0.99)
                    defer { window.close() }
                    if let output {
                        let data = try #require(bitmap.representation(using: .png, properties: [:]))
                        let filename = "settings\(section)\(state.rawValue)-\(name)-\(language)-680x420.png"
                        try data.write(to: output.appendingPathComponent(filename))
                    }
                }
            }
        }
    }
}

/// Compare rendered colors in the same display profile instead of assuming a backing bitmap is sRGB.
@MainActor private func renderedReferenceColor(_ color: Color) throws -> NSColor {
    let frame = NSRect(x: 0, y: 0, width: 10, height: 10)
    let hosting = NSHostingView(rootView: color.frame(width: 10, height: 10))
    hosting.frame = frame
    let window = NSWindow(contentRect: frame, styleMask: [.borderless], backing: .buffered, defer: false)
    window.isReleasedWhenClosed = false
    window.contentView = hosting
    hosting.layoutSubtreeIfNeeded()
    let bitmap = try #require(hosting.bitmapImageRepForCachingDisplay(in: hosting.bounds))
    hosting.cacheDisplay(in: hosting.bounds, to: bitmap)
    #expect(!window.isVisible)
    defer { window.close() }
    return try #require(bitmap.colorAt(x: bitmap.pixelsWide / 2, y: bitmap.pixelsHigh / 2)?.usingColorSpace(.sRGB))
}
