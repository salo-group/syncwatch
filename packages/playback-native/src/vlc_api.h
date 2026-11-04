/* SPDX-License-Identifier: Apache-2.0 */
#ifndef SYNCWATCH_VLC_API_H
#define SYNCWATCH_VLC_API_H
/* Compile against the actual pinned public headers; no copied VLC ABI. */
#include <stdbool.h>
#ifndef _WIN32
#include <sys/types.h>
#endif
#include "../../../3rdparty/vlc/include/vlc/libvlc.h"
#include "../../../3rdparty/vlc/include/vlc/libvlc_renderer_discoverer.h"
#include "../../../3rdparty/vlc/include/vlc/libvlc_media.h"
#include "../../../3rdparty/vlc/include/vlc/libvlc_media_player.h"
#include "../../../3rdparty/vlc/include/vlc/libvlc_events.h"

#define SW_VLC_FUNCTIONS(X) \
 X(const char *, libvlc_get_version, (void)) \
 X(const char *, libvlc_get_changeset, (void)) \
 X(libvlc_instance_t *, libvlc_new, (int, const char *const *)) \
 X(void, libvlc_release, (libvlc_instance_t *)) \
 X(libvlc_media_t *, libvlc_media_new_fd, (libvlc_instance_t *, int)) \
 X(void, libvlc_media_add_option, (libvlc_media_t *, const char *)) \
 X(void, libvlc_media_add_option_flag, (libvlc_media_t *, const char *, unsigned)) \
 X(void, libvlc_media_release, (libvlc_media_t *)) \
 X(libvlc_media_player_t *, libvlc_media_player_new, (libvlc_instance_t *)) \
 X(void, libvlc_media_player_release, (libvlc_media_player_t *)) \
 X(void, libvlc_media_player_set_media, (libvlc_media_player_t *, libvlc_media_t *)) \
 X(int, libvlc_media_player_play, (libvlc_media_player_t *)) \
 X(int, libvlc_media_player_add_slave, (libvlc_media_player_t *, libvlc_media_slave_type_t, const char *, bool)) \
 X(void, libvlc_video_set_key_input, (libvlc_media_player_t *, unsigned)) \
 X(void, libvlc_video_set_mouse_input, (libvlc_media_player_t *, unsigned)) \
 X(void, libvlc_media_player_set_pause, (libvlc_media_player_t *, int)) \
 X(void, libvlc_media_player_stop, (libvlc_media_player_t *)) \
 X(void, libvlc_media_player_set_time, (libvlc_media_player_t *, libvlc_time_t)) \
 X(libvlc_time_t, libvlc_media_player_get_time, (libvlc_media_player_t *)) \
 X(libvlc_time_t, libvlc_media_player_get_length, (libvlc_media_player_t *)) \
 X(libvlc_state_t, libvlc_media_player_get_state, (libvlc_media_player_t *)) \
 X(int, libvlc_media_player_is_seekable, (libvlc_media_player_t *)) \
 X(float, libvlc_media_player_get_rate, (libvlc_media_player_t *)) \
 X(int, libvlc_media_player_set_rate, (libvlc_media_player_t *, float)) \
 X(unsigned, libvlc_media_player_has_vout, (libvlc_media_player_t *)) \
 X(int, libvlc_video_get_size, (libvlc_media_player_t *, unsigned, unsigned *, unsigned *)) \
 X(int, libvlc_audio_get_volume, (libvlc_media_player_t *)) \
 X(int, libvlc_audio_output_set, (libvlc_media_player_t *, const char *)) \
 X(int, libvlc_audio_set_volume, (libvlc_media_player_t *, int)) \
 X(int, libvlc_audio_get_mute, (libvlc_media_player_t *)) \
 X(void, libvlc_audio_set_mute, (libvlc_media_player_t *, int)) \
 X(libvlc_track_description_t *, libvlc_audio_get_track_description, (libvlc_media_player_t *)) \
 X(libvlc_track_description_t *, libvlc_video_get_spu_description, (libvlc_media_player_t *)) \
 X(void, libvlc_track_description_list_release, (libvlc_track_description_t *)) \
 X(int, libvlc_audio_get_track, (libvlc_media_player_t *)) \
 X(int, libvlc_video_get_spu, (libvlc_media_player_t *)) \
 X(int, libvlc_audio_set_track, (libvlc_media_player_t *, int)) \
 X(int, libvlc_video_set_spu, (libvlc_media_player_t *, int)) \
 X(libvlc_event_manager_t *, libvlc_media_player_event_manager, (libvlc_media_player_t *)) \
 X(int, libvlc_event_attach, (libvlc_event_manager_t *, libvlc_event_type_t, libvlc_callback_t, void *)) \
 X(void, libvlc_event_detach, (libvlc_event_manager_t *, libvlc_event_type_t, libvlc_callback_t, void *))

typedef struct sw_vlc_api {
#define SW_DECLARE(result, name, arguments) result (*name) arguments;
    SW_VLC_FUNCTIONS(SW_DECLARE)
#undef SW_DECLARE
    void (*set_surface)(libvlc_media_player_t *, void *);
    void (*set_xwindow)(libvlc_media_player_t *, uint32_t);
} sw_vlc_api;
_Static_assert(__builtin_types_compatible_p(__typeof__(&libvlc_media_player_set_xwindow),
    void (*)(libvlc_media_player_t *, uint32_t)), "VLC ABI: X11 drawable");
/* Header drift is a compilation error on every supported compiler. */
#define SW_CHECK(result, name, arguments) \
 _Static_assert(__builtin_types_compatible_p(__typeof__(&name), result (*) arguments), "VLC ABI: " #name);
SW_VLC_FUNCTIONS(SW_CHECK)
#undef SW_CHECK
typedef struct sw_library sw_library;
int sw_library_open(const char *, sw_vlc_api *, int, sw_library **);
int sw_library_prepare_output(const char *, int);
void sw_library_close(sw_library *);
#endif
