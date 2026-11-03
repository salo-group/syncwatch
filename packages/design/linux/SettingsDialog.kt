// SPDX-License-Identifier: Apache-2.0
package syncwatch.design

import androidx.compose.runtime.*


// First-party source only; compiling/rendering remains gated by the Linux dependency closure.
enum class SettingsVisualState { editing, savedNotice, invalidConfiguration }
class SettingsEditor private constructor(
    initial: Map<String, String>, val editableFields: Set<String>, val canSave: Boolean,
    val state: SettingsVisualState, val message: String, baseline: Map<String, String>?,
) {
    val values: Map<String, String> = (SyncWatchSettings.defaults + initial).toMap()
    private val original: Map<String, String> = (baseline ?: values).toMap()
    constructor(values: Map<String, String> = emptyMap(), editableFields: Set<String> = emptySet(), canSave: Boolean = false,
        state: SettingsVisualState = SettingsVisualState.editing,
        message: String = if (state == SettingsVisualState.invalidConfiguration) "invalidConfiguration" else "") :
        this(values.toMap(), editableFields.toSet(), canSave, state, message, null)
    private fun updated(values: Map<String, String> = this.values, state: SettingsVisualState = this.state,
        message: String = this.message, original: Map<String, String> = this.original) =
        SettingsEditor(values, editableFields, canSave, state, message, original)
    fun edit(id: String, value: String): SettingsEditor = if (id in editableFields && SyncWatchSettings.fields.any { it.id == id && it.kind != "folder" })
        updated(values = values + (id to value), state = SettingsVisualState.editing, message = "") else this
    val isValid: Boolean get() = values.keys == SyncWatchSettings.defaults.keys && editableFields.all { it in SyncWatchSettings.defaults } &&
        SyncWatchSettings.fields.all { descriptor -> values[descriptor.id]?.let { value ->
            value.length <= descriptor.maxLength && value.none { it.code < 32 || it.code in 127..159 } &&
                (descriptor.kind != "choice" || value in descriptor.options) && (descriptor.kind != "boolean" || value in setOf("true", "false"))
        } == true }
    fun save(persist: (Map<String, String>) -> Boolean): Pair<SettingsEditor, Boolean> {
        if (!canSave) return this to false
        if (!isValid) return updated(state = SettingsVisualState.invalidConfiguration, message = "invalidConfiguration") to false
        if (!persist(values.toMap())) return updated(state = SettingsVisualState.invalidConfiguration, message = "saveFailed") to false
        return updated(original = values.toMap(), state = SettingsVisualState.savedNotice, message = "changesSaved") to true
    }
    fun cancel() = updated(values = original.toMap(), state = SettingsVisualState.editing, message = "")
}

@Composable fun SettingsDialog(editor:SettingsEditor,onEditorChange:(SettingsEditor)->Unit,language:String="en",initialSection:String="interface",theme:SyncWatchTheme=SyncWatchTheme.dark,
    folderPath:String="",canChooseFolder:Boolean=false,onChooseFolder:()->Unit={},
    onSave:(Map<String,String>)->Boolean={false},onClose:()->Unit={}) {
    var section by remember {mutableStateOf(initialSection.takeIf{it in SyncWatchSettings.sections}?:"interface")}
    DesignSurface("Settings",DesignBindings.settings(editor,section,folderPath,canChooseFolder),language,theme){event->when(event.action){
        "selectSection"->event.value?.takeIf{it in SyncWatchSettings.sections}?.let{section=it}
        "editField"->if(event.payload!=null&&event.value!=null)onEditorChange(editor.edit(event.payload,event.value))
        "chooseFolder"->if(canChooseFolder && editor.canSave)onChooseFolder()
        "save"->onEditorChange(editor.save(onSave).first)
        "close","cancel"->{onEditorChange(editor.cancel());onClose()}
    }}
}
