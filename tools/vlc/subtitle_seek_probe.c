/* SPDX-License-Identifier: Apache-2.0 */
#include "probe_common.h"
#include <libavformat/avformat.h>
#include <libavutil/buffer.h>
#include <libavutil/mathematics.h>

int main(int argc, char **argv) {
    int result = 0;
    AVFormatContext *format = NULL;
    AVPacket *packet = NULL;
    unsigned restored = 0;
    const int64_t positions[] = {500, 3000, 5000, 500, 7950, 0, 500};
    const int64_t bounded_positions[] = {500, 3000, 500};
    REQUIRE(argc == 4, "usage: subtitle-seek-probe media library normal|damaged");
    const int bounded = strncmp(argv[3], "bounded-", 8) == 0;
    const unsigned seeks = bounded ? 3 : 7;
    REQUIRE(owned_library(argv[2]) && load_owned_dependencies(), "unowned codec closure");
    packet = av_packet_alloc();
    REQUIRE(packet && avformat_open_input(&format, argv[1], NULL, NULL) >= 0 &&
            avformat_find_stream_info(format, NULL) >= 0, "cannot open subtitle seek fixture");
    for (unsigned i = 0; i < seeks; ++i) {
        const int64_t target = bounded ? bounded_positions[i] : positions[i];
        unsigned active = 0;
        size_t retained = 0;
        int64_t video = -1;
        REQUIRE(av_seek_frame(format, -1, target * 1000, AVSEEK_FLAG_BACKWARD) >= 0, "seek failed");
        for (unsigned n = 0; n < 2500 && av_read_frame(format, packet) >= 0; ++n) {
            AVStream *stream = format->streams[packet->stream_index];
            const AVRational milliseconds = {1, 1000};
            const int64_t pts = packet->pts == AV_NOPTS_VALUE ? -1 :
                av_rescale_q(packet->pts, stream->time_base, milliseconds);
            const int64_t duration = av_rescale_q(packet->duration, stream->time_base, milliseconds);
            if (stream->codecpar->codec_type == AVMEDIA_TYPE_SUBTITLE &&
                stream->codecpar->codec_id == AV_CODEC_ID_ASS && pts >= 0 && pts <= target &&
                duration > 0 && target - pts < duration) {
                ++active;
                retained += sizeof(*packet) + (packet->buf ? packet->buf->size : (size_t)packet->size);
                retained += (size_t)packet->side_data_elems * sizeof(*packet->side_data);
                for (int j = 0; j < packet->side_data_elems; j++) retained += packet->side_data[j].size;
            }
            if (stream->codecpar->codec_type == AVMEDIA_TYPE_VIDEO) {
                if (video < 0) video = pts;
                if (pts >= target + 300) { av_packet_unref(packet); break; }
            }
            av_packet_unref(packet);
        }
        fprintf(stderr, "subtitle seek: target=%lld active=%u bytes=%zu video=%lld\n",
                (long long)target, active, retained, (long long)video);
        REQUIRE(video >= 0 && llabs(video - target) <= 40, "A/V seek position changed");
        const unsigned expected = bounded ? (strcmp(argv[3], "bounded-oversized") == 0 ? 0 :
            strcmp(argv[3], "bounded-aggregate") == 0 ? 2 : 1) :
            strcmp(argv[3], "normal") == 0 ? target < 7900 : target == 0;
        REQUIRE(!bounded || retained <= 4 * 1024 * 1024, "decoded subtitle retention exceeds 4 MiB");
        REQUIRE(active == expected, "active/expired/duplicate subtitle mismatch");
        if (target && active) ++restored;
    }
    printf("{\"seeks\":%u,\"restored\":%u,\"ownedLibraryLoaded\":true,\"physicalOutputAccepted\":false}\n", seeks, restored);
cleanup:
    av_packet_free(&packet);
    avformat_close_input(&format);
    release_owned_dependencies();
    return result;
}
