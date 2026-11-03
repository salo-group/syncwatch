/* SPDX-License-Identifier: Apache-2.0
 * Decoder reset regression. Native clock progress alone is insufficient.
 */
#include "probe_common.h"

static unsigned decoded_video(libvlc_media_t *media) {
    libvlc_media_stats_t stats = {0};
    return libvlc_media_get_stats(media, &stats) ? (unsigned)stats.i_decoded_video : 0;
}

static void settle(unsigned period) {
    uint64_t end = milliseconds() + period;
    while (milliseconds() < end) delay();
}

int main(int argc, char **argv) {
    const char *options[] = {"--ignore-config", "--no-plugins-cache", "--no-media-library",
        "--no-video-title-show", "--no-osd", "--no-sub-autodetect-file",
        "--avcodec-hw=none", "--vout=dummy", "--aout=dummy", "--quiet"};
    libvlc_instance_t *instance = NULL;
    libvlc_media_t *media = NULL;
    libvlc_media_player_t *player = NULL;
    unsigned before = 0, after = 0;
    int result = 0, clock = 0;
    REQUIRE(argc == 3, "sparse authored media and owned runtime required");
    REQUIRE(owned_library(argv[2]) && load_owned_dependencies(), "owned dependency closure");
    REQUIRE(strcmp(libvlc_get_changeset(), SW_VLC_REVISION) == 0, "native source revision");
    instance = libvlc_new(sizeof(options) / sizeof(options[0]), options);
    REQUIRE(instance, "native instance");
    media = libvlc_media_new_path(instance, argv[1]);
    REQUIRE(media, "authored sparse media");
    libvlc_media_add_option(media, ":start-paused");
    player = libvlc_media_player_new_from_media(media);
    REQUIRE(player && libvlc_media_player_play(player) == 0 && wait_state(player, libvlc_Paused), "paused load");
    libvlc_media_player_set_pause(player, 0);
    REQUIRE(wait_state(player, libvlc_Playing), "warm play");
    settle(3500);
    REQUIRE(decoded_video(media) > 0, "initial decoded video");
    libvlc_media_player_set_pause(player, 1);
    REQUIRE(wait_state(player, libvlc_Paused), "warm pause");
    for (unsigned i = 0; i < 30; ++i) {
        libvlc_media_player_set_time(player, 905 + (i + 1) * 1000);
        settle(500);
    }
    libvlc_media_player_set_pause(player, 0);
    REQUIRE(wait_state(player, libvlc_Playing), "resume after paused seeks");
    settle(500);
    before = decoded_video(media);
    libvlc_media_player_set_time(player, 13000);
    uint64_t end = milliseconds() + 4000;
    while (milliseconds() < end) {
        after = decoded_video(media);
        clock = libvlc_media_player_get_state(player) == libvlc_Playing &&
                libvlc_media_player_get_time(player) >= 14000;
        if (clock && after >= before + 4) break;
        delay();
    }
    fprintf(stderr, "seek reset: decoded before=%u after=%u nativeClock=%d\n", before, after, clock);
    REQUIRE(clock && after >= before + 4, "fresh decoded video and native clock after Playing seek");
cleanup:
    if (player) { libvlc_media_player_stop(player); libvlc_media_player_release(player); }
    if (media) libvlc_media_release(media);
    if (instance) libvlc_release(instance);
    if (!result) printf("{\"success\":true,\"pausedSeeks\":30,\"decodedBefore\":%u,\"decodedAfter\":%u,"
        "\"actualClock\":true,\"normalClose\":true,\"ownedLibraryLoaded\":true,\"physicalOutputAccepted\":false}\n", before, after);
    return result;
}
