/* SPDX-License-Identifier: Apache-2.0
 * Real native controls with callback sinks; this does not accept physical output.
 */
#include "probe_common.h"
#include "library.c"
#include "derived-playback.c"

static atomic_uint white_pixels;
/* One atomic snapshot keeps PCM sample/crossing counts consistent. Only the
 * audio callback writes previous_sample; readers never reset live counters. */
static atomic_uint_fast64_t pcm_totals;

static void capture_pcm(void *opaque, const void *samples, unsigned count, int64_t pts) {
    struct observations *observed = opaque;
    const int16_t *pcm = samples;
    unsigned crossings = 0;
    (void)pts;
    for (unsigned i = 0; i < count; ++i) {
        int value = pcm[i * 2];
        if (observed->previous_sample <= 0 && value > 0) ++crossings;
        observed->previous_sample = value;
    }
    atomic_fetch_add(&pcm_totals, ((uint64_t)count << 32) | crossings);
}

static void display_caption(void *opaque, void *picture) {
    struct observations *observed = opaque;
    unsigned white = 0;
    for (unsigned y = HEIGHT / 2; y < HEIGHT; ++y) {
        for (unsigned x = 0; x < WIDTH; ++x) {
            const unsigned char *pixel = observed->pixels + y * PITCH + x * 4;
            if (pixel[0] > 240 && pixel[1] > 240 && pixel[2] > 240) ++white;
        }
    }
    atomic_store(&white_pixels, white);
    display_video(opaque, picture);
}

static int ready_native(sw_playback *player, int paused, int64_t position, int seek) {
    uint64_t end = milliseconds() + 4000;
    while (milliseconds() < end) {
        sw_observation observation;
        if (sw_playback_observe(player, 1, &observation) != SW_OK || observation.status == SW_FAILED) return 0;
        if (observation.status == SW_READY && observation.paused == paused &&
            (!seek || llabs(observation.position_ms - position) <= 100)) return 1;
        delay();
    }
    return 0;
}

static unsigned tone(void) {
    uint64_t start = atomic_load(&pcm_totals), current;
    uint64_t end = milliseconds() + 2000;
    do {
        current = atomic_load(&pcm_totals);
        if ((current >> 32) - (start >> 32) >= 13230) break;
        delay();
    } while (milliseconds() < end);
    unsigned samples = (unsigned)((current >> 32) - (start >> 32));
    unsigned crossings = (unsigned)current - (unsigned)start;
    return samples >= 13230 ? (unsigned)((uint64_t)crossings * 44100 / samples) : 0;
}

static int selected_native(sw_playback *player, int subtitles, int32_t expected) {
    int32_t actual = -1;
    return sw_playback_selected_track(player, 1, subtitles, &actual) == SW_OK && actual == expected;
}

int main(int argc, char **argv) {
    struct observations observed = {0};
    sw_playback *player = NULL;
    int result = 0;
    unsigned frequency = 0, white = 0;
    int32_t ids[256], previous[256], audio = -1, subtitle = -1;
    size_t count = 0, previous_count = 0;
    int external = argc == 5;
    REQUIRE(argc == 3 || external, "media, owned runtime and optional external audio/SRT required");
    REQUIRE(sw_playback_open(argv[2], SW_VLC_REVISION, 0, NULL, &player) == SW_OK, "native open");
#define BIND_OUTPUT(name) \
    __typeof__(&name) name##_owned; \
    do { void *address = symbol(player->runtime->library, #name); \
         REQUIRE(address, "owned output callback API missing"); \
         _Static_assert(sizeof(address) == sizeof(name##_owned), "Callback ABI"); \
         memcpy(&name##_owned, &address, sizeof(address)); } while (0)
    BIND_OUTPUT(libvlc_video_set_callbacks);
    BIND_OUTPUT(libvlc_video_set_format);
    BIND_OUTPUT(libvlc_audio_set_callbacks);
    BIND_OUTPUT(libvlc_audio_set_format);
#undef BIND_OUTPUT
    libvlc_video_set_callbacks_owned(player->player, lock_video, NULL, display_caption, &observed);
    libvlc_video_set_format_owned(player->player, "RV32", WIDTH, HEIGHT, PITCH);
    libvlc_audio_set_callbacks_owned(player->player, capture_pcm, NULL, NULL, NULL, NULL, &observed);
    libvlc_audio_set_format_owned(player->player, "S16N", 44100, 2);
    REQUIRE(sw_playback_load(player, 1, argv[1]) == SW_OK && ready_native(player, 1, 0, 0), "paused load");
    REQUIRE(sw_playback_tracks(player, 1, 0, ids, 256, &count) == SW_OK, "audio list");
    previous_count = count;
    memcpy(previous, ids, count * sizeof(*ids));
    for (size_t i = 0; i < count; ++i) if (ids[i] >= 0 && (audio < 0 || !external)) audio = ids[i];
    REQUIRE(audio >= 0 && sw_playback_select_track(player, 1, 0, audio) == SW_OK, "audio selection");
    if (external) {
        int32_t old_subtitles[256]; size_t old_count = 0;
        REQUIRE(sw_playback_tracks(player, 1, 1, old_subtitles, 256, &old_count) == SW_OK, "prior subtitle list");
        REQUIRE(sw_playback_attach_track(player, 1, 1, argv[4]) == SW_OK, "paused external subtitle attachment");
        uint64_t end = milliseconds() + 4000;
        do {
            REQUIRE(sw_playback_selected_track(player, 1, 1, &subtitle) == SW_OK, "external subtitle selection");
            int old = 0;
            for (size_t i = 0; i < old_count; ++i) if (old_subtitles[i] == subtitle) old = 1;
            if (subtitle >= 0 && !old) break;
            subtitle = -1;
            delay();
        } while (milliseconds() < end);
    }
    REQUIRE(sw_playback_tracks(player, 1, 1, ids, 256, &count) == SW_OK, "subtitle list");
    if (!external) for (size_t i = 0; i < count; ++i) if (ids[i] >= 0) { subtitle = ids[i]; break; }
    REQUIRE(subtitle >= 0 && sw_playback_select_track(player, 1, 1, subtitle) == SW_OK, "subtitle selection");
    if (external) {
        REQUIRE(sw_playback_attach_track(player, 1, 0, argv[3]) == SW_OK, "paused external audio attachment");
        uint64_t end = milliseconds() + 4000;
        int admitted = 0;
        do {
            REQUIRE(sw_playback_selected_track(player, 1, 0, &audio) == SW_OK, "external selected observation");
            int old = 0, present = 0;
            for (size_t i = 0; i < previous_count; ++i) if (previous[i] == audio) old = 1;
            REQUIRE(sw_playback_tracks(player, 1, 0, ids, 256, &count) == SW_OK, "external enumeration");
            for (size_t i = 0; i < count; ++i) if (ids[i] == audio) present = 1;
            admitted = audio >= 0 && !old && present;
            if (admitted) break;
            delay();
        } while (milliseconds() < end);
        REQUIRE(admitted, "external track not admitted");
        REQUIRE(sw_playback_pause(player, 1, 0) == SW_OK && ready_native(player, 0, 0, 0), "initial external resume");
        end = milliseconds() + 350;
        while (milliseconds() < end) delay();
        frequency = tone();
        REQUIRE(frequency >= 840 && frequency <= 920, "initial paused attachment PCM missing");
    }
    for (unsigned cycle = 0; cycle < 3; ++cycle) {
        int64_t target = cycle == 1 ? 500 : 1500;
        REQUIRE(sw_playback_pause(player, 1, 1) == SW_OK && ready_native(player, 1, 0, 0), "pause");
        REQUIRE(sw_playback_seek(player, 1, target) == SW_OK && ready_native(player, 1, target, 1), "paused seek");
        unsigned before_frames = atomic_load(&observed.frames);
        atomic_store(&white_pixels, 0);
        REQUIRE(sw_playback_pause(player, 1, 0) == SW_OK && ready_native(player, 0, 0, 0), "resume");
        uint64_t end = milliseconds() + 350;
        while (milliseconds() < end) delay();
        end = milliseconds() + 3500;
        do {
            frequency = tone();
            if (frequency >= 840 && frequency <= 920) break;
        } while (milliseconds() < end);
        fprintf(stderr, "cycle %u: PCM=%u Hz frames=%u caption=%u\n", cycle, frequency,
                atomic_load(&observed.frames), atomic_load(&white_pixels));
        REQUIRE(frequency >= 840 && frequency <= 920, "alternate PCM output missing after paused seek");
        end = milliseconds() + 1800;
        while (atomic_load(&white_pixels) < 20 && milliseconds() < end) delay();
        white = atomic_load(&white_pixels);
        REQUIRE(white >= 20 && white < WIDTH * HEIGHT / 4, "subtitle pixels missing after paused seek");
        REQUIRE(selected_native(player, 0, audio) && selected_native(player, 1, subtitle), "selected streams changed");
        sw_observation observation;
        REQUIRE(sw_playback_observe(player, 1, &observation) == SW_OK && observation.position_ms >= target + 100, "native clock did not advance");
        REQUIRE(atomic_load(&observed.frames) > before_frames + 1, "decoded video did not advance");
    }
cleanup:
    if (player) sw_playback_close(player);
    if (!result) printf("{\"success\":true,\"cycles\":3,\"alternateToneHz\":%u,\"whiteSubtitlePixels\":%u,"
                        "\"actualClock\":true,\"decodedVideoProgress\":true,\"selectedStreamsRetained\":true,\"normalClose\":true,\"physicalOutputAccepted\":false}\n",
                        frequency, white);
    return result;
}
