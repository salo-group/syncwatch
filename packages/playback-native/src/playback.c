/* SPDX-License-Identifier: Apache-2.0 */
#define _POSIX_C_SOURCE 200809L
#include "vlc_api.h"
#include "../include/syncwatch_playback.h"
#include <stdatomic.h>
#include <time.h>
#include <limits.h>
#ifdef _WIN32
#include <windows.h>
#endif
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <stdio.h>

static const char *const video_outputs[] = {"dummy", "macosx", "direct3d11", "xcb_x11"};
static const char *const audio_outputs[] = {"adummy", "auhal", "mmdevice", "pulse"};

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
    int played_video;
    int output;
    atomic_int selected_audio;
    atomic_uint_fast64_t sequence;
};
static const libvlc_event_type_t event_types[] = {
    libvlc_MediaPlayerOpening, libvlc_MediaPlayerBuffering, libvlc_MediaPlayerPlaying,
    libvlc_MediaPlayerPaused, libvlc_MediaPlayerStopped, libvlc_MediaPlayerEndReached,
    libvlc_MediaPlayerEncounteredError, libvlc_MediaPlayerTimeChanged,
    libvlc_MediaPlayerLengthChanged, libvlc_MediaPlayerSeekableChanged,
    libvlc_MediaPlayerVout, libvlc_MediaPlayerESAdded, libvlc_MediaPlayerESDeleted,
    libvlc_MediaPlayerESSelected, libvlc_MediaPlayerAudioVolume,
    libvlc_MediaPlayerMuted, libvlc_MediaPlayerUnmuted
};
static void changed(const libvlc_event_t *event, void *opaque) {
    sw_playback *owner = opaque;
    /* No allocation, locks, UI, managed delegate or libVLC reentry here. */
    if (event->type == libvlc_MediaPlayerESSelected &&
        event->u.media_player_es_changed.i_type == libvlc_track_audio)
        atomic_store_explicit(&owner->selected_audio, event->u.media_player_es_changed.i_id, memory_order_relaxed);
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
    p->played_video = 0;
    atomic_store_explicit(&p->selected_audio, INT_MIN, memory_order_relaxed);
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
static int valid_rate(double rate) {
    return isfinite(rate) && rate >= .25 && rate <= 4;
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
    char video[32], audio[32];
    snprintf(video, sizeof(video), "--vout=%s", video_outputs[output]);
    snprintf(audio, sizeof(audio), "--aout=%s", audio_outputs[output]);
    const char *options[] = {"--quiet", "--ignore-config", "--no-video-title-show", "--no-osd", "--no-snapshot-preview", "--no-plugins-cache",
        "--avcodec-hw=none", "--no-media-library", "--no-sub-autodetect-file",
        video, audio};
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
    atomic_init(&p->selected_audio, INT_MIN);
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
    /* Stop destroys VLC's audio output, including the one created with the
     * player. Recreate our fixed backend before starting the paused input so
     * volume can be applied before the first audible frame. The public setter
     * creates an idle output; it neither resumes playback nor selects a device. */
    if (p->api.libvlc_audio_output_set(p->player, audio_outputs[p->output])) {
        p->failed = 1; return SW_ENGINE;
    }
    if (sw_media_input_open(path, &p->input) != SW_INPUT_OK) { p->failed = 1; return SW_ENGINE; }
    int descriptor = sw_media_input_descriptor(p->input);
    if (descriptor < 0) { release_input(p); p->failed = 1; return SW_ENGINE; }
    libvlc_media_t *media = p->api.libvlc_media_new_fd(p->runtime->instance, descriptor);
    if (!media) { release_input(p); p->failed = 1; return SW_ENGINE; }
    p->api.libvlc_media_add_option(media, ":start-paused");
    /* Keep the local AppKit buffer below the paused preview lead-in.
     * VLC can reach EOF before showing a short tail with its default buffer. */
    if (p->output == SW_OUTPUT_APPKIT)
        p->api.libvlc_media_add_option_flag(media, ":file-caching=100", libvlc_media_option_trusted);
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
        if (!valid_rate(result->rate)) result->rate = 1;
    }
    result->video_outputs = (int32_t)p->api.libvlc_media_player_has_vout(p->player);
    if (state == libvlc_Playing && result->video_outputs > 0) p->played_video = 1;
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
    /* Resume changes pause state only. A second seek here can strand native
     * decoder picture queues, even after the input reports Playing. Stream
     * selection already refills its packets at the actual local position. */
    p->api.libvlc_media_player_set_pause(p->player, paused);
    return SW_OK;
}

/* Leave time for the normal seek readback within the bridge correction budget. */
enum {
    SW_PREROLL_MS = 800,
    SW_RESTORE_MS = 200,
    SW_POLL_MS = 10,
    SW_PREVIEW_RATE_DIVISOR = 4,
    SW_PREVIEW_MEDIA_SPAN_MS = (SW_PREROLL_MS + SW_RESTORE_MS) / SW_PREVIEW_RATE_DIVISOR
};

static uint64_t playback_monotonic_ms(void) {
#ifdef _WIN32
    return GetTickCount64();
#else
    struct timespec value;
    if (clock_gettime(CLOCK_MONOTONIC, &value)) return UINT64_MAX;
    return (uint64_t)value.tv_sec * 1000 + (uint64_t)value.tv_nsec / 1000000;
#endif
}

static int wait_playback_poll(uint64_t deadline) {
    if (playback_monotonic_ms() >= deadline) return 0;
#ifdef _WIN32
    Sleep(SW_POLL_MS);
#else
    const struct timespec interval = { 0, SW_POLL_MS * 1000000 };
    nanosleep(&interval, NULL);
#endif
    return playback_monotonic_ms() < deadline;
}

static int audio_track_matches(sw_playback *p, int selected) {
    return atomic_load_explicit(&p->selected_audio, memory_order_relaxed) == selected &&
        p->api.libvlc_audio_get_track(p->player) == selected;
}

static int wait_audio_track(sw_playback *p, int selected, uint64_t deadline) {
    do {
        /* The track getter includes queued requests. Require the input's
         * stream-selection event as well. */
        if (audio_track_matches(p, selected)) return playback_monotonic_ms() < deadline;
    } while (wait_playback_poll(deadline));
    return 0;
}

/* VLC can report a paused clock before presenting the requested picture.
 * Seek before previewing so the first displayed picture is near the target.
 * Disable the audio decoder during the preview. */
static int prime_paused_video(sw_playback *p, int64_t target, int64_t duration) {
    uint64_t started = playback_monotonic_ms();
    if (started == UINT64_MAX) return SW_ENGINE;
    libvlc_media_t *media = p->api.libvlc_media_player_get_media(p->player);
    libvlc_media_stats_t stats = {0};
    if (!media) return SW_UNAVAILABLE;
    if (!p->api.libvlc_media_get_stats(media, &stats)) {
        p->api.libvlc_media_release(media);
        return SW_UNAVAILABLE;
    }
    int previous_frames = stats.i_displayed_pictures;
    int previous_track = p->api.libvlc_audio_get_track(p->player);
    float previous_rate = p->api.libvlc_media_player_get_rate(p->player);
    if (!valid_rate(previous_rate)) {
        p->api.libvlc_media_release(media);
        return SW_UNAVAILABLE;
    }
    uint64_t deadline = started + SW_PREROLL_MS;
    int prepared = 0;
    int running = 0;
    if ((previous_track >= 0 && !wait_audio_track(p, previous_track, deadline)) ||
        (previous_track < 0 && p->api.libvlc_audio_get_track_count(p->player) > 0 &&
         !audio_track_matches(p, -1))) {
        p->api.libvlc_media_release(media);
        return SW_UNAVAILABLE;
    }
    /* Confirm that the input has no selected audio decoder before Play.
     * Leave the output's volume and mute settings in the device's control. */
    if (previous_track >= 0 && (p->api.libvlc_audio_set_track(p->player, -1) ||
        !wait_audio_track(p, -1, deadline))) goto failed;
    if (p->api.libvlc_audio_get_track(p->player) >= 0 || playback_monotonic_ms() >= deadline) goto failed;
    /* Leave enough media for the preview and pause before end of input.
     * This lead-in stays within the shared seek tolerance. */
    int64_t last_preview_start = duration - SW_PREVIEW_MEDIA_SPAN_MS;
    p->api.libvlc_media_player_set_time(p->player, target < last_preview_start ? target : last_preview_start);
    if (p->api.libvlc_media_player_set_rate(p->player, 1.f / SW_PREVIEW_RATE_DIVISOR)) goto failed;
    p->api.libvlc_media_player_set_pause(p->player, 0);
    do {
        if (p->api.libvlc_media_player_get_state(p->player) == libvlc_Playing) running = 1;
        if (p->api.libvlc_audio_get_track(p->player) >= 0 ||
            atomic_load_explicit(&p->selected_audio, memory_order_relaxed) >= 0) goto failed;
        if (running && p->api.libvlc_media_get_stats(media, &stats) &&
            stats.i_displayed_pictures > previous_frames && playback_monotonic_ms() < deadline) {
            prepared = 1;
            break;
        }
    } while (wait_playback_poll(deadline));
    /* An old Paused observation cannot confirm that a queued Play has settled. */
    if (!running) goto failed;
    p->api.libvlc_media_player_set_pause(p->player, 1);
    deadline = started + SW_PREROLL_MS + SW_RESTORE_MS;
    while (p->api.libvlc_media_player_get_state(p->player) != libvlc_Paused) {
        if (!wait_playback_poll(deadline)) goto failed;
    }
    if (playback_monotonic_ms() >= deadline) goto failed;
    /* Restore the rate request while paused, before the later track request
     * and before any user resume enters VLC's input queue. */
    if (p->api.libvlc_media_player_set_rate(p->player, previous_rate)) goto failed;
    if (previous_track >= 0 &&
        (p->api.libvlc_audio_set_track(p->player, previous_track) ||
         !wait_audio_track(p, previous_track, deadline))) goto failed;
    p->api.libvlc_media_release(media);
    if (!prepared || !valid_input(p) || playback_monotonic_ms() >= deadline) return SW_UNAVAILABLE;
    p->played_video = 1;
    return SW_OK;
failed:
    p->api.libvlc_media_release(media);
    release_input(p);
    p->api.libvlc_media_player_set_rate(p->player, previous_rate);
    p->failed = 1;
    return SW_ENGINE;
}

int sw_playback_seek(sw_playback *p, uint64_t generation, int64_t target) {
    int code = check(p, generation); if (code) return code;
    int64_t duration = p->api.libvlc_media_player_get_length(p->player);
    if (target < 0 || duration < 0 || target > duration || target > INT64_C(604800000)) return SW_INVALID;
    if (!p->api.libvlc_media_player_is_seekable(p->player)) return SW_UNAVAILABLE;
    int prepare = p->played_video ? p->output == SW_OUTPUT_APPKIT : target > 0;
    if (p->output != SW_OUTPUT_HEADLESS && prepare && target < duration && duration > SW_PREVIEW_MEDIA_SPAN_MS &&
        p->api.libvlc_media_player_get_state(p->player) == libvlc_Paused &&
        p->api.libvlc_video_get_track(p->player) >= 0)
        return prime_paused_video(p, target, duration);
    p->api.libvlc_media_player_set_time(p->player, target); return SW_OK;
}
int sw_playback_rate(sw_playback *p, uint64_t generation, double rate) {
    int code = check(p, generation); if (code) return code;
    if (!valid_rate(rate)) return SW_INVALID;
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
