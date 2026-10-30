/* SPDX-License-Identifier: Apache-2.0 */
#include "vlc_api.h"
#include "../include/syncwatch_playback.h"
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <stdio.h>

static const char *const video_outputs[] = {"dummy", "macosx", "direct3d11", "xcb_x11"};
static const char *const audio_options[] = {"--aout=dummy", "--aout=auhal", "--aout=mmdevice", "--aout=pulse"};

struct sw_runtime {
    sw_vlc_api api;
    sw_library *library;
    libvlc_instance_t *instance;
    int output;
    atomic_uint references;
};
typedef struct sw_external_input {
    sw_media_input *input;
    struct sw_external_input *next;
} sw_external_input;
struct sw_playback {
    sw_vlc_api api;
    sw_runtime *runtime;
    libvlc_media_player_t *player;
    sw_media_input *input;
    sw_external_input *external;
    libvlc_event_manager_t *events;
    unsigned attached;
    uint64_t generation;
    int loaded;
    int failed;
    int output;
    int refill_on_resume;
    atomic_uint_fast64_t sequence;
};
static const libvlc_event_type_t event_types[] = {
    libvlc_MediaPlayerOpening, libvlc_MediaPlayerBuffering, libvlc_MediaPlayerPlaying,
    libvlc_MediaPlayerPaused, libvlc_MediaPlayerStopped, libvlc_MediaPlayerEndReached,
    libvlc_MediaPlayerEncounteredError, libvlc_MediaPlayerTimeChanged,
    libvlc_MediaPlayerLengthChanged, libvlc_MediaPlayerSeekableChanged,
    libvlc_MediaPlayerVout, libvlc_MediaPlayerESAdded, libvlc_MediaPlayerESDeleted,
    libvlc_MediaPlayerESSelected
};
static void changed(const libvlc_event_t *event, void *opaque) {
    (void)event;
    sw_playback *owner = opaque;
    /* No allocation, locks, UI, managed delegate or libVLC reentry here. */
    atomic_fetch_add_explicit(&owner->sequence, 1, memory_order_relaxed);
}
static void release_input(sw_playback *p) {
    /* Stop joins VLC's input before its borrowed descriptor is released. */
    p->api.libvlc_media_player_stop(p->player);
    p->api.libvlc_media_player_set_media(p->player, NULL);
    while (p->external) {
        sw_external_input *entry = p->external;
        p->external = entry->next;
        sw_media_input_close(entry->input);
        free(entry);
    }
    sw_media_input_close(p->input);
    p->input = NULL;
    p->loaded = 0;
    p->refill_on_resume = 0;
}
static int valid_input(sw_playback *p) {
    sw_input_version version;
    int valid = p->loaded && sw_media_input_version(p->input, &version) == SW_INPUT_OK;
    for (sw_external_input *entry = p->external; valid && entry; entry = entry->next)
        valid = sw_media_input_version(entry->input, &version) == SW_INPUT_OK;
    if (valid) return 1;
    if (p->loaded) {
        release_input(p);
        p->failed = 1;
    }
    return 0;
}
static int check(sw_playback *p, uint64_t generation) {
    if (!p) return SW_INVALID;
    if (generation != p->generation) return SW_STALE;
    return valid_input(p) ? SW_OK : SW_UNAVAILABLE;
}
int sw_runtime_open(const char *path, const char *revision, int output, sw_runtime **result) {
    if (!result) return SW_INVALID;
    *result = NULL;
    if (!path || !revision || strlen(revision) != 40 || output < SW_OUTPUT_HEADLESS || output > SW_OUTPUT_X11) return SW_INVALID;
    const char *plugins = getenv("VLC_PLUGIN_PATH");
    if (plugins && *plugins) return SW_UNAVAILABLE;
#ifdef __APPLE__
    if (output != SW_OUTPUT_HEADLESS && output != SW_OUTPUT_APPKIT) return SW_INVALID;
#elif defined(_WIN32)
    if (output != SW_OUTPUT_HEADLESS && output != SW_OUTPUT_HWND) return SW_INVALID;
#else
    if (output != SW_OUTPUT_HEADLESS && output != SW_OUTPUT_X11) return SW_UNAVAILABLE;
#endif
    sw_runtime *p = calloc(1, sizeof(*p));
    if (!p) return SW_ENGINE;
    atomic_init(&p->references, 1);
    p->output = output;
    if (!sw_library_open(path, &p->api, output, &p->library)) { free(p); return SW_UNAVAILABLE; }
    if (strcmp(p->api.libvlc_get_version(), "3.0.24 Vetinari") || strcmp(p->api.libvlc_get_changeset(), revision)) {
        sw_runtime_close(p); return SW_UNAVAILABLE;
    }
    if (!sw_library_prepare_output(path, output)) { sw_runtime_close(p); return SW_UNAVAILABLE; }
    /* Never silently select hardware paths whose controls are unqualified. */
    char video[32];
    snprintf(video, sizeof(video), "--vout=%s", video_outputs[output]);
    const char *options[] = {"--quiet", "--ignore-config", "--no-video-title-show", "--no-osd", "--no-snapshot-preview", "--no-plugins-cache",
        "--avcodec-hw=none", "--no-media-library", "--no-sub-autodetect-file",
        video, audio_options[output]};
    p->instance = p->api.libvlc_new(sizeof(options) / sizeof(options[0]), options);
    if (!p->instance) { sw_runtime_close(p); return SW_ENGINE; }
    *result = p;
    return SW_OK;
}
void sw_runtime_close(sw_runtime *p) {
    if (!p || atomic_fetch_sub_explicit(&p->references, 1, memory_order_acq_rel) != 1) return;
    if (p->instance) p->api.libvlc_release(p->instance);
    sw_library_close(p->library);
    free(p);
}
int sw_playback_open_runtime(sw_runtime *runtime, void *surface, uint32_t drawable, sw_playback **result) {
    if (!result) return SW_INVALID;
    *result = NULL;
    if (!runtime || (runtime->output == SW_OUTPUT_HEADLESS ? surface != NULL || drawable != 0 :
        runtime->output == SW_OUTPUT_X11 ? surface != NULL || drawable == 0 : surface == NULL || drawable != 0)) return SW_INVALID;
    sw_playback *p = calloc(1, sizeof(*p));
    if (!p) return SW_ENGINE;
    atomic_init(&p->sequence, 0);
    atomic_fetch_add_explicit(&runtime->references, 1, memory_order_relaxed);
    p->runtime = runtime; p->api = runtime->api; p->output = runtime->output;
    p->player = p->api.libvlc_media_player_new(runtime->instance);
    if (!p->player) { sw_playback_close(p); return SW_ENGINE; }
    /* Application controls own user actions; native input must not bypass the port. */
    p->api.libvlc_video_set_key_input(p->player, 0);
    p->api.libvlc_video_set_mouse_input(p->player, 0);
    if (surface) p->api.set_surface(p->player, surface);
    if (drawable) p->api.set_xwindow(p->player, drawable);
    p->events = p->api.libvlc_media_player_event_manager(p->player);
    for (unsigned i = 0; i < sizeof(event_types) / sizeof(event_types[0]); ++i) {
        if (p->api.libvlc_event_attach(p->events, event_types[i], changed, p)) {
            sw_playback_close(p); return SW_ENGINE;
        }
        p->attached++;
    }
    *result = p;
    return SW_OK;
}
static int open_output(const char *path, const char *revision, int output, void *surface,
                       uint32_t drawable, sw_playback **result) {
    if (!result) return SW_INVALID;
    *result = NULL;
    if (output < SW_OUTPUT_HEADLESS || output > SW_OUTPUT_X11 ||
        (output == SW_OUTPUT_HEADLESS ? surface != NULL || drawable != 0 :
         output == SW_OUTPUT_X11 ? surface != NULL || drawable == 0 : surface == NULL || drawable != 0)) return SW_INVALID;
    sw_runtime *runtime = NULL;
    int status = sw_runtime_open(path, revision, output, &runtime);
    if (status != SW_OK) return status;
    status = sw_playback_open_runtime(runtime, surface, drawable, result);
    sw_runtime_close(runtime);
    return status;
}
int sw_playback_open(const char *path, const char *revision, int output, void *surface, sw_playback **result) {
    if (output == SW_OUTPUT_X11) {
        if (result) *result = NULL;
        return SW_INVALID;
    }
    return open_output(path, revision, output, surface, 0, result);
}
int sw_playback_open_x11(const char *path, const char *revision, uint32_t drawable, sw_playback **result) {
    return open_output(path, revision, SW_OUTPUT_X11, NULL, drawable, result);
}
int sw_playback_load(sw_playback *p, uint64_t generation, const char *path) {
    if (!p || !path || !path[0] || !generation || generation > UINT64_C(9007199254740991)) return SW_INVALID;
    if (generation <= p->generation) return SW_STALE;
    /* A local absolute file path only. No playlist, network URL or remote path. */
#ifdef _WIN32
    if (!((path[0] >= 'A' && path[0] <= 'Z') || (path[0] >= 'a' && path[0] <= 'z')) ||
        path[1] != ':' || (path[2] != '\\' && path[2] != '/')) return SW_INVALID;
#else
    if (path[0] != '/') return SW_INVALID;
#endif
    release_input(p);
    p->generation = generation;
    p->loaded = 0;
    p->failed = 0;
    if (sw_media_input_open(path, &p->input) != SW_INPUT_OK) { p->failed = 1; return SW_ENGINE; }
    int descriptor = sw_media_input_descriptor(p->input);
    if (descriptor < 0) { release_input(p); p->failed = 1; return SW_ENGINE; }
    libvlc_media_t *media = p->api.libvlc_media_new_fd(p->runtime->instance, descriptor);
    if (!media) { release_input(p); p->failed = 1; return SW_ENGINE; }
    p->api.libvlc_media_add_option(media, ":start-paused");
    /* Surface setters reset inherited VLC options. Fixed trusted per-media
     * options preserve software decoding and forbid a standalone HWND fallback. */
    p->api.libvlc_media_add_option_flag(media, ":avcodec-hw=none", libvlc_media_option_trusted);
    char video[32];
    snprintf(video, sizeof(video), ":vout=%s", video_outputs[p->output]);
    p->api.libvlc_media_add_option_flag(media, video, libvlc_media_option_trusted);
    if (p->output == SW_OUTPUT_HWND)
        p->api.libvlc_media_add_option_flag(media, ":window=embed-hwnd", libvlc_media_option_trusted);
    if (p->output == SW_OUTPUT_X11)
        p->api.libvlc_media_add_option_flag(media, ":window=embed-xid", libvlc_media_option_trusted);
    p->api.libvlc_media_player_set_media(p->player, media);
    p->api.libvlc_media_release(media);
    p->loaded = 1;
    if (p->api.libvlc_media_player_play(p->player)) { release_input(p); p->failed = 1; return SW_ENGINE; }
    return SW_OK;
}
int sw_playback_input_version(sw_playback *p, uint64_t generation, sw_input_version *result) {
    if (!result) return SW_INVALID;
    memset(result, 0, sizeof(*result));
    int code = check(p, generation); if (code) return code;
    if (sw_media_input_version(p->input, result) == SW_INPUT_OK) return SW_OK;
    release_input(p); p->failed = 1;
    return SW_UNAVAILABLE;
}
int sw_playback_read_at(sw_playback *p, uint64_t generation, uint64_t offset,
    unsigned char *buffer, size_t capacity, size_t *count) {
    if (count) *count = 0;
    int code = check(p, generation); if (code) return code;
    int status = sw_media_input_read_at(p->input, offset, buffer, capacity, count);
    if (status == SW_INPUT_OK) return SW_OK;
    if (status == SW_INPUT_INVALID) return SW_INVALID;
    release_input(p); p->failed = 1;
    return SW_UNAVAILABLE;
}
int sw_playback_observe(sw_playback *p, uint64_t generation, sw_observation *result) {
    if (!p || !result) return SW_INVALID;
    if (generation != p->generation) return SW_STALE;
    memset(result, 0, sizeof(*result));
    result->generation = p->generation;
    result->sequence = atomic_load_explicit(&p->sequence, memory_order_relaxed);
    result->duration_ms = -1; result->rate = 1; result->paused = 1;
    if (!valid_input(p)) { if (p->failed) result->status = SW_FAILED; return SW_OK; }
    libvlc_state_t state = p->api.libvlc_media_player_get_state(p->player);
    result->status = state == libvlc_Error ? SW_FAILED :
        (state == libvlc_Playing || state == libvlc_Paused || state == libvlc_Ended) ? SW_READY : SW_LOADING;
    result->buffering = state == libvlc_Buffering;
    result->ended = state == libvlc_Ended;
    if (result->status == SW_READY) {
        result->position_ms = p->api.libvlc_media_player_get_time(p->player);
        result->duration_ms = p->api.libvlc_media_player_get_length(p->player);
        if (result->duration_ms > INT64_C(604800000)) { result->status = SW_FAILED; result->position_ms = 0; result->duration_ms = -1; return SW_OK; }
        if (result->position_ms < 0) result->position_ms = 0;
        /* Some demuxers report EOF rather than Error for a corrupt/empty local
         * item. It has no playable timeline; do not acknowledge Ready/resume. */
        if (state == libvlc_Ended && result->duration_ms <= 0 && result->position_ms == 0) {
            result->status = SW_FAILED;
            return SW_OK;
        }
        if (result->duration_ms >= 0 && result->position_ms > result->duration_ms) result->position_ms = result->duration_ms;
        result->paused = state != libvlc_Playing;
        result->seekable = p->api.libvlc_media_player_is_seekable(p->player);
        result->rate = p->api.libvlc_media_player_get_rate(p->player);
        if (!isfinite(result->rate) || result->rate < .25 || result->rate > 4) result->rate = 1;
    }
    result->video_outputs = (int32_t)p->api.libvlc_media_player_has_vout(p->player);
    unsigned width = 0, height = 0;
    if (!p->api.libvlc_video_get_size(p->player, 0, &width, &height) && width <= INT32_MAX && height <= INT32_MAX) {
        result->width = (int32_t)width; result->height = (int32_t)height;
    }
    result->volume = p->api.libvlc_audio_get_volume(p->player);
    result->muted = p->api.libvlc_audio_get_mute(p->player) == 1;
    return SW_OK;
}
int sw_playback_pause(sw_playback *p, uint64_t generation, int paused) {
    int code = check(p, generation); if (code) return code;
    if (paused != 0 && paused != 1) return SW_INVALID;
    /* A stream selected on pause may be acknowledged before its decoder has
     * consumed the demux refill. Refill once more when playback resumes, after
     * that selection has settled, using the actual clock rather than a cached
     * seek request. Keep this local pipeline detail below all client ports. */
    int64_t refill_position = -1;
    if (!paused && p->refill_on_resume) {
        if (p->api.libvlc_media_player_is_seekable(p->player))
            refill_position = p->api.libvlc_media_player_get_time(p->player);
        p->refill_on_resume = 0;
    }
    /* Queue resume before the refill. A second seek while still paused can
     * leave the input waiting on decoder pictures before it handles resume. */
    p->api.libvlc_media_player_set_pause(p->player, paused);
    if (refill_position >= 0)
        p->api.libvlc_media_player_set_time(p->player, refill_position);
    return SW_OK;
}
int sw_playback_seek(sw_playback *p, uint64_t generation, int64_t target) {
    int code = check(p, generation); if (code) return code;
    int64_t duration = p->api.libvlc_media_player_get_length(p->player);
    if (target < 0 || duration < 0 || target > duration || target > INT64_C(604800000)) return SW_INVALID;
    if (!p->api.libvlc_media_player_is_seekable(p->player)) return SW_UNAVAILABLE;
    /* A paused seek can discard packets for the selected streams just as a
     * paused selection does. Rearm even after an earlier resume consumed the
     * marker; connection setup may already have resumed this input once. */
    if (p->api.libvlc_media_player_get_state(p->player) == libvlc_Paused)
        p->refill_on_resume = 1;
    p->api.libvlc_media_player_set_time(p->player, target); return SW_OK;
}
int sw_playback_rate(sw_playback *p, uint64_t generation, double rate) {
    int code = check(p, generation); if (code) return code;
    if (!isfinite(rate) || rate < .25 || rate > 4) return SW_INVALID;
    return p->api.libvlc_media_player_set_rate(p->player, (float)rate) ? SW_ENGINE : SW_OK;
}
int sw_playback_volume(sw_playback *p, uint64_t generation, double volume, int muted) {
    int code = check(p, generation); if (code) return code;
    if (!isfinite(volume) || volume < 0 || volume > 1 || (muted != 0 && muted != 1)) return SW_INVALID;
    if (p->api.libvlc_audio_set_volume(p->player, (int)lround(volume * 100))) return SW_ENGINE;
    p->api.libvlc_audio_set_mute(p->player, muted); return SW_OK;
}
int sw_playback_stop(sw_playback *p, uint64_t generation) {
    if (!p) return SW_INVALID;
    if (generation != p->generation) return SW_STALE;
    release_input(p);
    p->failed = 0; return SW_OK;
}
static libvlc_track_description_t *tracks(sw_playback *p, int subtitles) {
    return subtitles ? p->api.libvlc_video_get_spu_description(p->player) : p->api.libvlc_audio_get_track_description(p->player);
}
int sw_playback_tracks(sw_playback *p, uint64_t generation, int subtitles, int32_t *ids, size_t capacity, size_t *count) {
    int code = check(p, generation); if (code) return code;
    if (!count || (capacity && !ids) || capacity > 256 || (subtitles != 0 && subtitles != 1)) return SW_INVALID;
    libvlc_track_description_t *head = tracks(p, subtitles); size_t total = 0;
    for (libvlc_track_description_t *t = head; t; t = t->p_next) {
        if (total < capacity) ids[total] = t->i_id;
        total++;
    }
    p->api.libvlc_track_description_list_release(head); *count = total;
    return total <= capacity ? SW_OK : SW_UNAVAILABLE;
}
int sw_playback_track_name(sw_playback *p, uint64_t generation, int subtitles, int32_t id, char *utf8, size_t capacity) {
    int code = check(p, generation); if (code) return code;
    if (!utf8 || !capacity || capacity > 65536 || (subtitles != 0 && subtitles != 1)) return SW_INVALID;
    utf8[0] = 0; int result = SW_UNAVAILABLE;
    libvlc_track_description_t *head = tracks(p, subtitles);
    for (libvlc_track_description_t *t = head; t; t = t->p_next) {
        if (t->i_id == id && t->psz_name) {
            size_t size = strlen(t->psz_name);
            if (size < capacity) { memcpy(utf8, t->psz_name, size + 1); result = SW_OK; }
            break;
        }
    }
    p->api.libvlc_track_description_list_release(head); return result;
}
int sw_playback_selected_track(sw_playback *p, uint64_t generation, int subtitles, int32_t *id) {
    int code = check(p, generation); if (code) return code;
    if (!id || (subtitles != 0 && subtitles != 1)) return SW_INVALID;
    *id = subtitles ? p->api.libvlc_video_get_spu(p->player) : p->api.libvlc_audio_get_track(p->player);
    return SW_OK;
}
int sw_playback_attach_track(sw_playback *p, uint64_t generation, int subtitles, const char *path) {
    int code = check(p, generation); if (code) return code;
    if (!path || !*path || (subtitles != 0 && subtitles != 1)) return SW_INVALID;
#ifdef _WIN32
    if (!((path[0] >= 'A' && path[0] <= 'Z') || (path[0] >= 'a' && path[0] <= 'z')) ||
        path[1] != ':' || (path[2] != '\\' && path[2] != '/')) return SW_INVALID;
#else
    if (path[0] != '/') return SW_INVALID;
#endif
    sw_external_input *entry = calloc(1, sizeof(*entry));
    if (!entry) return SW_ENGINE;
    if (sw_media_input_open(path, &entry->input) != SW_INPUT_OK) { free(entry); return SW_UNAVAILABLE; }
    int descriptor = sw_media_input_descriptor(entry->input);
    char uri[32];
    int size = snprintf(uri, sizeof(uri), "fd://%d", descriptor);
    if (descriptor < 0 || size < 0 || (size_t)size >= sizeof(uri) ||
        p->api.libvlc_media_player_add_slave(p->player,
            subtitles ? libvlc_media_slave_type_subtitle : libvlc_media_slave_type_audio, uri, true)) {
        sw_media_input_close(entry->input); free(entry); return SW_ENGINE;
    }
    entry->next = p->external;
    p->external = entry;
    if (!valid_input(p)) return SW_UNAVAILABLE;
    if (p->api.libvlc_media_player_get_state(p->player) == libvlc_Paused) p->refill_on_resume = 1;
    return SW_OK;
}
int sw_playback_select_track(sw_playback *p, uint64_t generation, int subtitles, int32_t id) {
    int code = check(p, generation); if (code) return code;
    if (subtitles != 0 && subtitles != 1) return SW_INVALID;
    libvlc_track_description_t *head = tracks(p, subtitles); int found = 0;
    for (libvlc_track_description_t *t = head; t; t = t->p_next) if (t->i_id == id) found = 1;
    p->api.libvlc_track_description_list_release(head);
    if (!found) return SW_INVALID;
    int64_t position = p->api.libvlc_media_player_get_time(p->player);
    int seekable = p->api.libvlc_media_player_is_seekable(p->player);
    if (subtitles ? p->api.libvlc_video_set_spu(p->player, id) : p->api.libvlc_audio_set_track(p->player, id)) return SW_ENGINE;
    if (p->api.libvlc_media_player_get_state(p->player) == libvlc_Paused)
        p->refill_on_resume = 1;
    /* The demuxer may have discarded packets for an unselected stream while
     * prebuffering. Refill from the actual local clock without changing pause
     * state or emitting a shared seek intent. Selection alone is not output. */
    if (seekable && position >= 0) p->api.libvlc_media_player_set_time(p->player, position);
    return SW_OK;
}
void sw_playback_close(sw_playback *p) {
    if (!p) return;
    /* detach synchronizes against in-flight callbacks before freeing opaque. */
    while (p->attached) p->api.libvlc_event_detach(p->events, event_types[--p->attached], changed, p);
    if (p->player) {
        release_input(p);
        if (p->api.set_surface) p->api.set_surface(p->player, NULL);
        if (p->api.set_xwindow) p->api.set_xwindow(p->player, 0);
        p->api.libvlc_media_player_release(p->player);
    }
    sw_runtime_close(p->runtime);
    free(p);
}
