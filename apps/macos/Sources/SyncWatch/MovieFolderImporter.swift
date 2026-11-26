import SwiftUI
import SyncWatchDesign
import UniformTypeIdentifiers

/// Present the native folder importer from the view that owns the action.
struct MovieFolderImporter: ViewModifier {
    @Binding var isPresented: Bool
    let language: String
    let onResult: @MainActor (Result<URL, any Error>) -> Void

    func body(content: Content) -> some View {
        let importer = content.fileImporter(isPresented: $isPresented, allowedContentTypes: [.folder]) { result in
            Task { @MainActor in onResult(result) }
        }
        if #available(macOS 14.0, *) {
            importer
                .fileDialogMessage(SyncWatchCopy.text("folder", language: language))
                .fileDialogConfirmationLabel(SyncWatchCopy.text("chooseFolder", language: language))
        } else {
            importer
        }
    }
}
