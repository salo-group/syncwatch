/* SPDX-License-Identifier: Apache-2.0 */
#include "probe_common.h"

static atomic_uint white_pixels;

static void display_subtitle(void *opaque, void *picture) {
    struct observations *o = opaque;
    unsigned white = 0;
    for (unsigned y = HEIGHT / 2; y < HEIGHT; ++y)
        for (unsigned x = 0; x < WIDTH; ++x) {
            const unsigned char *pixel = o->pixels + y * PITCH + x * 4;
            if (pixel[0] > 240 && pixel[1] > 240 && pixel[2] > 240) ++white;
        }
    atomic_store(&white_pixels, white);
    display_video(opaque, picture);
}

static void reset_audio(struct observations *o) {
    atomic_store(&o->audio_peak, 0);
    atomic_store(&o->audio_samples, 0);
    atomic_store(&o->positive_crossings, 0);
}

int main(int argc, char **argv) {
    struct observations observed = {0};
    libvlc_instance_t *instance = NULL;
    libvlc_media_t *media = NULL;
    libvlc_media_player_t *player = NULL;
    int result = 0;
    unsigned frequency = 0, peak = 0, white = 0;
    uint64_t deadline;
    const char *options[20] = {"--ignore-config", "--no-plugins-cache", "--no-media-library",
        "--no-video-title-show", "--no-osd", "--no-sub-autodetect-file", "--avcodec-hw=none",
        "--codec=avcodec,rawvideo,araw", "--vout=vmem", "--aout=amem", "--quiet"};
    int count = 11;
    if (argc < 4 || argc > 5) {
        fprintf(stderr, "usage: effects-probe mode media library [external-uri]\n"); return 2;
    }
    int adjust = strcmp(argv[1], "adjust") == 0;
    int normalize = strcmp(argv[1], "normalize") == 0;
    int tempo = strcmp(argv[1], "tempo") == 0;
    int embedded_srt = strcmp(argv[1], "embedded-srt") == 0;
    int embedded_ass = strcmp(argv[1], "embedded-ass") == 0;
    int embedded_audio = strcmp(argv[1], "embedded-audio") == 0;
    int external_subtitle = strcmp(argv[1], "srt") == 0 || strcmp(argv[1], "ass") == 0;
    int external_audio = strcmp(argv[1], "external-audio") == 0;
    int subtitle = external_subtitle || embedded_srt || embedded_ass;
    int external = external_audio || embedded_audio;
    REQUIRE(adjust || normalize || tempo || subtitle || external, "unknown effect case");
    REQUIRE(argc == ((external_subtitle || external_audio) ? 5 : 4), "external media URI is required");
    REQUIRE(owned_library(argv[3]), "unowned native library");
    REQUIRE(load_owned_dependencies(), "cannot load the owned native dependency closure");
    if (adjust) {
        options[count++] = "--video-filter=adjust";
        options[count++] = "--brightness=1.5";
    }
    if (normalize) {
        options[count++] = "--audio-filter=normvol";
        options[count++] = "--norm-buff-size=1";
        options[count++] = "--norm-max-level=1.0";
    }
    if (tempo) options[count++] = "--audio-filter=scaletempo";
    instance = libvlc_new(count, options);
    REQUIRE(instance, "cannot create media instance");
    libvlc_log_set(instance, observe_log, &observed);
    REQUIRE(strcmp(libvlc_get_changeset(), SW_VLC_REVISION) == 0, "wrong native source revision");
    media = libvlc_media_new_path(instance, argv[2]);
    REQUIRE(media, "cannot create media");
    player = libvlc_media_player_new_from_media(media);
    REQUIRE(player, "cannot create player");
    libvlc_video_set_callbacks(player, lock_video, NULL, display_subtitle, &observed);
    libvlc_video_set_format(player, "RV32", WIDTH, HEIGHT, PITCH);
    libvlc_audio_set_callbacks(player, play_audio, NULL, NULL, NULL, NULL, &observed);
    libvlc_audio_set_format(player, "S16N", 44100, 2);
    if (external_subtitle)
        REQUIRE(libvlc_media_player_add_slave(player, subtitle ? libvlc_media_slave_type_subtitle : libvlc_media_slave_type_audio,
                                             argv[4], true) == 0, "external track rejected");
    REQUIRE(libvlc_media_player_play(player) == 0 && wait_state(player, libvlc_Playing), "play failed");
    REQUIRE(wait_frames(&observed, 3, 1), "decoded frames missing");
    if (external_audio)
        REQUIRE(libvlc_media_player_add_slave(player, libvlc_media_slave_type_audio, argv[4], true) == 0,
                "external audio track rejected while playing");
    if (embedded_srt || embedded_ass || embedded_audio) {
        libvlc_track_description_t *head = embedded_audio ? libvlc_audio_get_track_description(player)
                                                         : libvlc_video_get_spu_description(player);
        int index = 0, selected = -1;
        int expected = embedded_audio ? 1 : embedded_ass ? 2 : 0;
        for (libvlc_track_description_t *track = head; track; track = track->p_next)
            if (track->i_id >= 0 && index++ == expected) selected = track->i_id;
        libvlc_track_description_list_release(head);
        REQUIRE(selected >= 0, "embedded track is missing");
        REQUIRE((embedded_audio ? libvlc_audio_set_track(player, selected) : libvlc_video_set_spu(player, selected)) == 0,
                "embedded track selection rejected");
        /* Match the adapter's local-clock refill of previously discarded packets. */
        if (libvlc_media_player_is_seekable(player)) {
            libvlc_time_t position = libvlc_media_player_get_time(player);
            if (position >= 0) libvlc_media_player_set_time(player, position);
        }
    }
    if (adjust) REQUIRE(atomic_load(&observed.luminance) >= 160, "brightness did not change decoded pixels");
    if (subtitle) {
        deadline = milliseconds() + 1800;
        /* A uniformly bright authored video frame cannot count as subtitle text. */
        while (((white = atomic_load(&white_pixels)) < 20 || white > WIDTH * HEIGHT / 4) && milliseconds() < deadline) delay();
        fprintf(stderr, "subtitle observations: case=%s white=%u frames=%u track=%d tracks=%d time=%lld state=%d\n",
                argv[1], white, atomic_load(&observed.frames), libvlc_video_get_spu(player),
                libvlc_video_get_spu_count(player), (long long)libvlc_media_player_get_time(player),
                (int)libvlc_media_player_get_state(player));
        REQUIRE(white >= 20 && white <= WIDTH * HEIGHT / 4 && libvlc_video_get_spu(player) >= 0,
                "subtitle track did not render actual text pixels");
    }
    if (tempo) REQUIRE(libvlc_media_player_set_rate(player, 1.5f) == 0, "tempo rejected");
    if (normalize || external || tempo) {
        // Let the filter settle, then measure a fresh PCM window. Never accept
        // a selected track/filter solely from its requested API setting.
        deadline = milliseconds() + 350;
        while (milliseconds() < deadline) delay();
        reset_audio(&observed);
        deadline = milliseconds() + 1500;
        while (atomic_load(&observed.audio_samples) < 13230 && milliseconds() < deadline) delay();
        unsigned samples = atomic_load(&observed.audio_samples);
        REQUIRE(samples >= 13230, "filtered or external PCM samples missing");
        frequency = (unsigned)((uint64_t)atomic_load(&observed.positive_crossings) * 44100 / samples);
        peak = atomic_load(&observed.audio_peak);
        fprintf(stderr, "effect PCM observations: peak=%u frequency=%u\n", peak, frequency);
        if (external) REQUIRE(frequency >= 840 && frequency <= 920 && peak >= 2500 && peak <= 3500,
                               "external audio did not replace the embedded tone");
        if (normalize) REQUIRE(frequency >= 400 && frequency <= 480 && peak >= 1000 && peak < 3000,
                                "normalization did not reduce actual PCM amplitude");
        if (tempo) REQUIRE(frequency >= 400 && frequency <= 480,
                            "time stretching changed the decoded pitch");
    }
    printf("{\"case\":\"%s\",\"decodedPixel\":%u,\"whiteSubtitlePixels\":%u,"
           "\"toneHz\":%u,\"audioPeak\":%u,\"ownedLibraryLoaded\":true,\"physicalOutputAccepted\":false}\n",
           argv[1], atomic_load(&observed.luminance), white, frequency, peak);
cleanup:
    if (player) { libvlc_media_player_stop(player); libvlc_media_player_release(player); }
    if (media) libvlc_media_release(media);
    if (instance) libvlc_release(instance);
    release_owned_dependencies();
    return result;
}
