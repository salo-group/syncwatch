/* SPDX-License-Identifier: Apache-2.0 */
#define _FILE_OFFSET_BITS 64
#define _POSIX_C_SOURCE 200809L

#include "memory.h"
#include "syncwatch_media_input.h"

#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <libavformat/avformat.h>
#include <libavutil/error.h>
#include <libavutil/mathematics.h>
#include <libavutil/mem.h>
#include <libavutil/time.h>

enum { READ_LIMIT = 512 * 1024 };
enum { MAX_PACKETS = 800, MAX_CUES = 8, TIME_LIMIT_US = 1500000, IO_BUFFER_SIZE = 4096 };

typedef struct {
    sw_media_input *input;
    int64_t size;
    int64_t position;
    int64_t deadline;
    size_t bytes;
    unsigned packets;
    bool read_limit;
    bool failed;
} fixture_reader;

static int expired(void *opaque) {
    const fixture_reader *reader = opaque;
    return av_gettime_relative() >= reader->deadline;
}

static int read_fixture(void *opaque, uint8_t *buffer, int size) {
    fixture_reader *reader = opaque;
    size_t remaining = READ_LIMIT - reader->bytes;
    if (reader->failed)
        return AVERROR(EIO);
    if (expired(reader))
        return AVERROR_EXIT;
    if (!remaining) {
        reader->read_limit = true;
        return AVERROR_EXIT;
    }
    if (size <= 0)
        return AVERROR(EINVAL);
    size_t wanted = (size_t)size < remaining ? (size_t)size : remaining;
    if (wanted > IO_BUFFER_SIZE)
        wanted = IO_BUFFER_SIZE;
    size_t used;
    if (sw_media_input_read_at(reader->input, (uint64_t)reader->position, buffer, wanted, &used) != SW_INPUT_OK) {
        reader->failed = true;
        return AVERROR(EIO);
    }
    reader->bytes += used;
    reader->position += (int64_t)used;
    return used ? (int)used : AVERROR_EOF;
}

static int64_t seek_fixture(void *opaque, int64_t offset, int whence) {
    fixture_reader *reader = opaque;
    if (whence == AVSEEK_SIZE)
        return reader->size;
    if (expired(reader))
        return AVERROR_EXIT;
    whence &= ~AVSEEK_FORCE;
    int64_t base = 0;
    switch (whence) {
    case SEEK_SET: break;
    case SEEK_CUR: base = reader->position; break;
    case SEEK_END: base = reader->size; break;
    default: return AVERROR(EINVAL);
    }
    if (base < 0 || base > reader->size || offset < -base || offset > reader->size - base)
        return AVERROR(EINVAL);
    reader->position = base + offset;
    return reader->position;
}

static bool cue_is_active(AVFormatContext *format, fixture_reader *reader, AVPacket *packet,
                          int track, int64_t cue, int64_t target, int *caption_bytes) {
    while (!expired(reader) && !reader->read_limit && reader->packets < MAX_PACKETS) {
        int status = av_read_frame(format, packet);
        if (status < 0) {
            if (status != AVERROR_EOF && status != AVERROR_EXIT)
                reader->failed = true;
            return false;
        }
        reader->packets++;
        bool selected = packet->stream_index == track && packet->pts != AV_NOPTS_VALUE;
        bool found = selected && packet->pts == cue && packet->duration > target - cue;
        bool done = selected && packet->pts >= cue;
        if (found)
            *caption_bytes = packet->size;
        av_packet_unref(packet);
        if (done)
            return found;
    }
    return false;
}

static int64_t find_active_cue(AVFormatContext *format, fixture_reader *reader, AVPacket *packet,
                               int video, int track, int64_t target_ms, int *caption_bytes) {
    AVStream *pictures = format->streams[video];
    AVStream *captions = format->streams[track];
    const AVRational milliseconds = {1, 1000};
    int64_t target = av_rescale_q(target_ms, milliseconds, captions->time_base);
    /* The first video seek loads deferred Cues through the public demuxer API. */
    if (av_seek_frame(format, video, 0, AVSEEK_FLAG_BACKWARD) < 0) {
        reader->failed = true;
        return -1;
    }
    int index = av_index_search_timestamp(captions, target, AVSEEK_FLAG_BACKWARD);
    int64_t candidates[MAX_CUES];
    int count = 0;
    for (; index >= 0 && count < MAX_CUES; index--) {
        const AVIndexEntry *entry = avformat_index_get_entry(captions, index);
        if (!entry || entry->timestamp < 0 || entry->timestamp > target)
            break;
        candidates[count++] = entry->timestamp;
    }
    for (int i = 0; i < count && !reader->read_limit && !reader->failed && !expired(reader); i++) {
        int64_t where = av_rescale_q(candidates[i], captions->time_base, pictures->time_base);
        if (av_seek_frame(format, video, where, AVSEEK_FLAG_BACKWARD) < 0) {
            reader->failed = true;
            break;
        }
        if (cue_is_active(format, reader, packet, track, candidates[i], target, caption_bytes))
            return av_rescale_q(candidates[i], captions->time_base, milliseconds);
    }
    return -1;
}

static bool number(const char *text, unsigned long long max, unsigned long long *value) {
    char *end;
    errno = 0;
    *value = strtoull(text, &end, 10);
    return text[0] >= '0' && text[0] <= '9' && !*end && !errno && *value <= max;
}

int main(int argc, char **argv) {
    unsigned long long ordinal, target_ms, limit = SW_SUBTITLE_HEAP_LIMIT;
    if ((argc != 4 && argc != 5) || !number(argv[2], 65535, &ordinal) ||
        !number(argv[3], INT32_MAX, &target_ms) ||
        (argc == 5 && !number(argv[4], SW_SUBTITLE_HEAP_LIMIT, &limit)) ||
        !sw_subtitle_memory_begin((size_t)limit))
        return 2;
    fixture_reader reader = {.deadline = av_gettime_relative() + TIME_LIMIT_US};
    AVFormatContext *format = NULL;
    AVIOContext *io = NULL;
    AVPacket *packet = NULL;
    int64_t active = -1;
    int caption_bytes = 0;
    bool parsed = false, stable = false, have_extent = false;
    uint64_t size, initial_position;
    if (sw_media_input_open(argv[1], &reader.input) != SW_INPUT_OK ||
        sw_media_input_extent(reader.input, &size, &initial_position) != SW_INPUT_OK ||
        !size || size > INT64_MAX)
        goto cleanup;
    have_extent = true;
    reader.size = (int64_t)size;
    format = avformat_alloc_context();
    if (!format)
        goto cleanup;
    uint8_t *buffer = av_malloc(IO_BUFFER_SIZE);
    if (!buffer)
        goto cleanup;
    io = avio_alloc_context(buffer, IO_BUFFER_SIZE, 0, &reader, read_fixture, NULL, seek_fixture);
    if (!io) {
        av_free(buffer);
        goto cleanup;
    }
    format->pb = io;
    format->flags |= AVFMT_FLAG_CUSTOM_IO;
    format->interrupt_callback = (AVIOInterruptCB){expired, &reader};
    av_log_set_level(AV_LOG_QUIET);
    if (avformat_open_input(&format, NULL, av_find_input_format("matroska"), NULL) < 0)
        goto cleanup;
    int video = -1;
    int track = -1;
    unsigned count = 0;
    for (unsigned i = 0; i < format->nb_streams; i++) {
        AVCodecParameters *codec = format->streams[i]->codecpar;
        if (video < 0 && codec->codec_type == AVMEDIA_TYPE_VIDEO)
            video = (int)i;
        if (codec->codec_id == AV_CODEC_ID_ASS && count++ == ordinal)
            track = (int)i;
    }
    parsed = true;
    if (video < 0 || track < 0)
        goto cleanup;
    packet = av_packet_alloc();
    if (packet)
        active = find_active_cue(format, &reader, packet, video, track, (int64_t)target_ms, &caption_bytes);

cleanup:
    av_packet_free(&packet);
    avformat_close_input(&format);
    if (io) {
        av_freep(&io->buffer);
        avio_context_free(&io);
    }
    if (have_extent) {
        uint64_t current_size, current_position;
        stable = sw_media_input_extent(reader.input, &current_size, &current_position) == SW_INPUT_OK &&
                 current_size == size && current_position == initial_position;
    }
    sw_media_input_close(reader.input);
    sw_subtitle_memory_result memory;
    if (!sw_subtitle_memory_finish(&memory)) {
        fputs("The demuxer retained charged allocations after cleanup\n", stderr);
        return 1;
    }
    bool work_limit = expired(&reader) || reader.packets >= MAX_PACKETS;
    if (!stable || reader.failed || reader.read_limit || work_limit || memory.refused) {
        active = -1;
        caption_bytes = 0;
    }
    printf("{\"activeStartMs\":%" PRId64 ",\"captionBytes\":%d,\"sourceBytes\":%zu,"
           "\"peakHeapBytes\":%zu,\"heapLimited\":%s,\"readLimited\":%s,\"workLimited\":%s,"
           "\"pinnedInputStable\":%s,\"packets\":%u}\n",
           active, caption_bytes, reader.bytes, memory.peak_bytes,
           memory.refused ? "true" : "false", reader.read_limit ? "true" : "false",
           work_limit ? "true" : "false", stable ? "true" : "false", reader.packets);
    bool limited = reader.read_limit || work_limit || memory.refused;
    return stable && (limited || (parsed && !reader.failed)) ? 0 : 1;
}
