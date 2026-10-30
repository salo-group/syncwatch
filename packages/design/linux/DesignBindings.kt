// SPDX-License-Identifier: Apache-2.0
package syncwatch.design

// Observations and event/model lowering only. The binding layer contains no visual specification.
internal object DesignBindings {
    private fun capabilities(actions: Set<String>, allows: (String)->Boolean) = actions.associate { "can.$it" to allows(it).toString() }
    private fun sections(ids: List<String>, selected: String) = ids.map { DesignContext(mapOf("id" to it,"selected" to (it==selected).toString())) }
    fun library(p: LibraryPresentation, query: String, language: String): DesignContext {
        val unknown = SyncWatchCopy.text("unknown", language)
        val duration = p.metadataDurationMs?.takeIf { it > 0 }?.let { ms ->
            SyncWatchCopy.text("metadataDurationFormat", language).replace("{hours}", (ms / 3_600_000).toString())
                .replace("{minutes}", (ms / 60_000 % 60).toString().padStart(2, '0'))
                .replace("{seconds}", (ms / 1_000 % 60).toString().padStart(2, '0'))
        }
        return DesignContext(capabilities(SyncWatchCopy.libraryActions,p::allows)+mapOf(
        "metadataDuration" to (duration ?: unknown),
        "metadataResolution" to if (p.metadataWidth > 0 && p.metadataHeight > 0)
            SyncWatchCopy.text("metadataResolutionFormat", language).replace("{width}", p.metadataWidth.toString()).replace("{height}", p.metadataHeight.toString()) else unknown,
        "metadataAudio" to (p.metadataAudioName?.ifEmpty { unknown } ?: p.metadataAudioTracks?.toString() ?: unknown),
        "metadataSubtitles" to (if (p.metadataSubtitlesDisabled || p.metadataSubtitleTracks == 0) SyncWatchCopy.text("metadataNoSubtitles", language) else p.metadataSubtitleName?.ifEmpty { unknown } ?: p.metadataSubtitleTracks?.toString() ?: unknown),
        "metadataLoading" to p.metadataLoading.toString(), "metadataUnavailable" to p.metadataUnavailable.toString(),
        "namespace" to "library","query" to query,"entryCount" to p.entries.size.toString(),"filesLabel" to SyncWatchCopy.text("files",language),"hasEntries" to p.entries.isNotEmpty().toString(),
        "hasSelection" to (p.selected!=null).toString(),"selectedTitle" to p.selected?.title.orEmpty(),"selectedLocation" to p.selected?.location.orEmpty(),
        "emptyMessage" to if(p.state==LibraryVisualState.scanning)"scanning"else"empty","missingMedia" to (p.state==LibraryVisualState.missingMedia).toString(),"roomMessage" to if(p.roomStatus.isNotEmpty())p.roomStatus else if(p.state==LibraryVisualState.reconnecting)"reconnecting"else"disconnected"),
        mapOf("entries" to p.entries.filter{query.isEmpty()||it.title.contains(query,ignoreCase=true)}.map{DesignContext(mapOf("id" to it.id,"title" to it.title,"folderMovie" to it.folderMovie.toString(),"selected" to (it.id==p.selectedID).toString(),
            "rowDuration" to (it.durationMs?.takeIf { ms -> ms > 0 }?.let { ms ->
                SyncWatchCopy.text("libraryRowDurationFormat", language).replace("{hours}", (ms / 3_600_000).toString())
                    .replace("{minutes}", (ms / 60_000 % 60).toString().padStart(2, '0'))
            } ?: "")))}))
    }
    fun player(p: PlayerPresentation) = DesignContext(capabilities(SyncWatchPlayer.actions,p::allows)+mapOf(
        "namespace" to "player","title" to p.title,"state" to p.state.name,
        "roomNotice" to p.roomNotice,"hasRoomNotice" to p.roomNotice.isNotEmpty().toString(),"position" to p.displayedPosition.toString(),"duration" to p.displayedDuration.toString(),
        "positionText" to PlayerPresentation.time(p.displayedPosition),"durationText" to PlayerPresentation.time(p.displayedDuration),"volume" to (if(p.isValid)p.volume else 0.0).toString(),
        "playIcon" to if(p.playing)"player.pause"else"player.play","playLabel" to if(p.playing)"pause"else"play","volumeIcon" to if(p.muted)"player.muted"else"player.volume","volumeLabel" to if(p.muted)"unmuteMovie"else"muteMovie"))
    fun settings(editor: SettingsEditor, section: String, folderPath: String = "", canChooseFolder: Boolean = false): DesignContext {
        val fields=SyncWatchSettings.fields.filter{it.section==section}.map{f->DesignContext(mapOf("id" to f.id,"label" to f.label,"kind" to if(f.kind in setOf("choice","boolean","folder"))f.kind else "text","value" to (if(f.kind=="folder") folderPath else editor.values[f.id].orEmpty()),"canChooseFolder" to (canChooseFolder && editor.canSave).toString(),"editable" to (f.id in editor.editableFields).toString(),"isRange" to "false"),mapOf("options" to f.options.map{DesignContext(mapOf("id" to it,"label" to "option"+it.replaceFirstChar{c->c.uppercase()}))}))}
        return DesignContext(mapOf("namespace" to "settings","section" to section,"dialogTitle" to "openSettings","status" to if(!editor.canSave)"settingsReadOnly"else if(editor.state==SettingsVisualState.savedNotice)"changesSaved"else editor.message,
            "saved" to (editor.state==SettingsVisualState.savedNotice).toString(),"can.save" to editor.canSave.toString(),"can.close" to "true","can.cancel" to "true","interfaceSection" to (section=="interface").toString(),"unavailable" to SyncWatchSettings.fields.none{it.section==section&&(it.id in editor.editableFields||(it.kind=="folder"&&canChooseFolder&&editor.canSave))}.toString()),
            mapOf("sections" to sections(SyncWatchSettings.sections,section),"fields" to fields))
    }
    fun playerSettings(editor: PlayerSettingsEditor, section: String, language: String): DesignContext {
        val fields=SyncWatchPlayerSettings.fields.filter{it.section==section}
        val rows=fields.map{it.row}.distinct().sorted().map{row->DesignContext(mapOf("id" to row.toString()),mapOf("fields" to fields.filter{it.row==row}.map{f->
            val raw=editor.values[f.id].orEmpty();val safe=if(PlayerSettingsEditor.valid(raw,f))raw else SyncWatchPlayerSettings.defaults.getValue(f.id)
            DesignContext(mapOf("id" to f.id,"label" to f.label,"kind" to f.kind,"value" to raw,"number" to safe,"minimum" to f.minimum.toString(),"maximum" to f.maximum.toString(),"step" to f.step.toString(),"unitText" to if(f.unit=="unitNone")""else" "+SyncWatchCopy.text(f.unit,language),"isRange" to (f.kind=="range").toString(),"editable" to editor.allows(f.id).toString()),
                mapOf("options" to f.options.map{o->DesignContext(mapOf("id" to o.id,"fieldID" to f.id,"label" to o.label,"selected" to (o.id==raw).toString(),"swatchColor" to (o.colorToken?.let{"@$it"}?:"")))}))
        }))}
        return preview(editor.values).merge(DesignContext(mapOf("namespace" to "playerSettings","section" to section,"dialogTitle" to "playerSettings","status" to if(!editor.canSave)"settingsReadOnly"else editor.message,"saved" to (editor.state==PlayerSettingsVisualState.savedNotice).toString(),"can.save" to editor.canSave.toString(),"can.resetSection" to editor.canReset(section).toString(),"can.close" to "true","can.cancel" to "true","showSubtitle" to (section=="subtitles").toString()),mapOf("sections" to sections(SyncWatchPlayerSettings.sections,section),"rows" to rows)))
    }
    fun preview(values: Map<String,String>): DesignContext {
        fun setting(id:String):String {val f=SyncWatchPlayerSettings.fields.single{it.id==id};return values[id]?.takeIf{PlayerSettingsEditor.valid(it,f)}?:SyncWatchPlayerSettings.defaults.getValue(id)}
        val v=listOf("subtitleSize","subtitlePadding","subtitleOffset","subtitleFont","outlineWidth").associateWith(::setting).toMutableMap();v["subtitleTop"]=(setting("subtitlePosition")=="top").toString()
        for((binding,id)in listOf("subtitleFill" to "subtitleColor","outlineFill" to "outlineColor"))v[binding]="@"+SyncWatchPlayerSettings.fields.single{it.id==id}.options.single{it.id==setting(id)}.colorToken
        return DesignContext(v)
    }
    fun call(p: CallPresentation, hasRemote: Boolean, hasLocal: Boolean): DesignContext {
        val v = mutableMapOf("showRemote" to (p.showsRemoteSurface && hasRemote).toString(), "showLocal" to (p.showsLocalPreview && hasLocal).toString(),
            "previewLabel" to p.previewLabel(hasLocal), "callStateLabel" to "callState" + p.state.name.replaceFirstChar { it.uppercase() },
            "maximumExtent" to SyncWatchCall.maximumExtent.toString(), "volume" to p.displayedVolume.toString(), "pinned" to p.pinned.toString(), "fullscreen" to p.fullscreen.toString(), "minimized" to p.minimized.toString(),
            "microphoneEnabled" to (p.microphoneEnabled == true).toString(), "cameraEnabled" to (p.cameraEnabled == true).toString(), "microphoneOff" to (p.microphoneEnabled == false).toString(), "cameraOff" to (p.cameraEnabled == false).toString(),
            "nextMicrophone" to if(p.microphoneEnabled == true) "0" else "1", "nextCamera" to if(p.cameraEnabled == true) "0" else "1",
            "pinLabel" to if(p.pinned) "unpinCall" else "pinCall", "fullscreenLabel" to if(p.fullscreen) "exitFullscreenCall" else "fullscreenCall", "fullscreenIcon" to if(p.fullscreen) "call.restore" else "call.fullscreen", "volumeIcon" to if(p.displayedVolume > 0) "call.volume" else "call.muted",
            "microphoneLabel" to if(p.microphoneEnabled == true) "microphoneOn" else "microphoneOff", "cameraLabel" to if(p.cameraEnabled == true) "cameraOn" else "cameraOff")
        for(action in SyncWatchCall.actions) v["can."+action] = p.allows(action).toString()
        return DesignContext(v)
    }
}
