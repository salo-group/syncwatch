/* SyncWatch modified 2026-11-05: recover indexed subtitles crossing a seek.
 * Inserted into the pinned LGPL matroskadec.c; original grants are retained.
 * Only earlier, duration-indexed blocks are read. A/V seek position is unchanged.
 */

/* Bounded optional preroll: malformed/incomplete cues leave normal seeking intact. */
#define SW_CUE_BLOCK_LIMIT (256 * 1024)
#include "libavutil/buffer_internal.h"

_Static_assert(sizeof(PacketListEntry) + sizeof(AVBuffer) + sizeof(AVBufferRef) <=
               SW_CUE_PACKET_OVERHEAD, "Review the pinned packet storage budget");

static int sw_cue_block_budget(MatroskaTrack *track, const AVBufferRef *buf, int laces,
                               const MatroskaBlockMore *more, int count, int64_t discard_padding)
{
    if (track->sw_preroll_remaining <= 0 || laces <= 0 || laces > 256 || count < 0)
        return 0;
    uint64_t per_packet = SW_CUE_PACKET_OVERHEAD;
    if (!track->needs_decoding)
        per_packet += buf->size;
    if (track->stream->codecpar->codec_id == AV_CODEC_ID_WEBVTT) {
        /* Identifier/settings split the input; each side buffer also has padding. */
        per_packet += 2 * (sizeof(AVPacketSideData) + AV_INPUT_BUFFER_PADDING_SIZE);
    } else {
        for (int i = 0; i < count; i++) {
            if (more[i].additional.size < 0)
                return 0;
            if (more[i].additional.size) {
                const uint64_t side = FFMAX((uint64_t)more[i].additional.size + 8,
                                            sizeof(AVDynamicHDRPlus));
                per_packet += side + sizeof(AVPacketSideData) + AV_INPUT_BUFFER_PADDING_SIZE;
            }
        }
        if (discard_padding)
            per_packet += 10 + sizeof(AVPacketSideData) + AV_INPUT_BUFFER_PADDING_SIZE;
    }
    if (per_packet > (uint64_t)track->sw_preroll_remaining / laces)
        return 0;
    track->sw_preroll_remaining -= per_packet * laces;
    if (!track->sw_preroll_remaining)
        track->sw_preroll_remaining = -1;
    return 1;
}

static int sw_cue_packet_bytes(const AVPacket *packet, uint64_t available, uint64_t *bytes)
{
    if (packet->size < 0 || packet->side_data_elems < 0)
        return 0;
    uint64_t size = SW_CUE_PACKET_OVERHEAD + (packet->buf ? packet->buf->size : (uint64_t)packet->size);
    if (size > available || (uint64_t)packet->side_data_elems >
        (available - size) / sizeof(*packet->side_data))
        return 0;
    size += (uint64_t)packet->side_data_elems * sizeof(*packet->side_data);
    for (int i = 0; i < packet->side_data_elems; i++) {
        if (available - size < AV_INPUT_BUFFER_PADDING_SIZE ||
            packet->side_data[i].size > available - size - AV_INPUT_BUFFER_PADDING_SIZE)
            return 0;
        size += packet->side_data[i].size + AV_INPUT_BUFFER_PADDING_SIZE;
    }
    *bytes = size;
    return 1;
}

static int sw_cue_element(MatroskaDemuxContext *m, int64_t end,
                          uint32_t *id, uint64_t *length)
{
    AVIOContext *pb = m->ctx->pb;
    uint64_t value;
    int n;
    if (avio_tell(pb) < 0 || avio_tell(pb) >= end)
        return AVERROR_INVALIDDATA;
    n = ebml_read_num(m, pb, 4, &value, 1);
    if (n < 0)
        return n;
    *id = value | (1U << (7 * n));
    n = ebml_read_length(m, pb, length);
    if (n < 0)
        return n;
    if (avio_tell(pb) > end || *length > end - avio_tell(pb))
        return AVERROR_INVALIDDATA;
    return 0;
}

/* Validate both the referenced cluster and the block's actual track before the
 * original parser can modify any track state. No intervening A/V is decoded.
 */
static int sw_cue_prepare(MatroskaDemuxContext *m, MatroskaIndexPos *cue,
                          int64_t cluster_pos, int64_t *block_pos,
                          uint64_t *block_bytes)
{
    AVIOContext *pb = m->ctx->pb;
    uint64_t length, cluster_length, timecode = 0, track = 0;
    int64_t start, end, block_end;
    uint32_t id;
    int found = 0;
    if (avio_seek(pb, cluster_pos, SEEK_SET) < 0 ||
        avio_rb32(pb) != MATROSKA_ID_CLUSTER ||
        ebml_read_length(m, pb, &cluster_length) < 0)
        return 0;
    start = avio_tell(pb);
    if (start < 0 || cluster_length > INT64_MAX - start ||
        cue->relative >= cluster_length)
        return 0;
    end = start + cluster_length;
    /* Read at most 64 small header elements and 64 KiB before Timestamp. */
    for (unsigned i = 0; i < 64 && avio_tell(pb) - start < 65536; i++) {
        if (sw_cue_element(m, end, &id, &length) < 0)
            return 0;
        if (id == MATROSKA_ID_CLUSTERTIMECODE) {
            if (length > 8 || ebml_read_uint(pb, length, 0, &timecode) < 0 ||
                pb->eof_reached || pb->error)
                return 0;
            found = 1;
            break;
        }
        if (id == MATROSKA_ID_BLOCKGROUP || id == MATROSKA_ID_SIMPLEBLOCK ||
            length > 65536 || avio_skip(pb, length) < 0)
            return 0;
    }
    *block_pos = start + cue->relative;
    if (!found || *block_pos < avio_tell(pb) ||
        avio_seek(pb, *block_pos, SEEK_SET) < 0 ||
        sw_cue_element(m, end, &id, &length) < 0 ||
        length > SW_CUE_BLOCK_LIMIT)
        return 0;
    block_end = avio_tell(pb) + length;
    *block_bytes = block_end - *block_pos;
    if (id == MATROSKA_ID_SIMPLEBLOCK) {
        if (length < 4 || ebml_read_num(m, pb, 8, &track, 1) < 0)
            return 0;
    } else if (id == MATROSKA_ID_BLOCKGROUP) {
        /* Reject duplicate Blocks, wrong-track cues, and excessive metadata. */
        unsigned children = 0;
        while (avio_tell(pb) < block_end && children++ < 64) {
            if (sw_cue_element(m, block_end, &id, &length) < 0)
                return 0;
            int64_t next = avio_tell(pb) + length;
            if (id == MATROSKA_ID_BLOCK) {
                if (track || length < 4 || ebml_read_num(m, pb, 8, &track, 1) < 0)
                    return 0;
            }
            if (avio_seek(pb, next, SEEK_SET) < 0)
                return 0;
        }
        if (avio_tell(pb) != block_end)
            return 0;
    } else {
        return 0;
    }
    if (track != cue->track || pb->eof_reached || pb->error ||
        avio_seek(pb, *block_pos, SEEK_SET) < 0)
        return 0;
    m->num_levels = 2;
    /* Limit the original parser to precisely this validated block element. */
    m->levels[1] = (MatroskaLevel) { start, block_end - start };
    m->current_cluster.pos = cluster_pos;
    m->current_cluster.timecode = timecode;
    m->current_id = 0;
    return 1;
}

static int sw_restore_indexed_subtitles(MatroskaDemuxContext *m, AVStream *stream,
                                        int64_t timestamp, int64_t resume_pos)
{
    const int old_skip = m->skip_to_keyframe;
    const uint64_t old_time = m->skip_to_timecode;
    const int64_t target = av_rescale_q(timestamp, stream->time_base, AV_TIME_BASE_Q);
    MatroskaIndex *entries = m->index.elem;
    PacketList preroll = { 0 };
    AVPacket packet = { 0 };
    int64_t seen[SW_CUE_COUNT_LIMIT];
    unsigned count = 0, attempts = 0, packets = 0;
    uint64_t bytes = 0, retained = 0;
    int ret = 0, moved = 0;
    if (target < 0 || m->segment_start < 0 || !m->time_scale || m->time_scale > INT64_MAX)
        return 0;
    for (unsigned i = 0; i < m->index.nb_elem; i++) {
        if (entries[i].time > INT64_MAX)
            continue;
        const int64_t start = av_rescale(entries[i].time, m->time_scale, 1000);
        if (start < 0 || start > target)
            continue;
        MatroskaIndexPos *positions = entries[i].pos.elem;
        for (unsigned j = 0; j < entries[i].pos.nb_elem; j++) {
            MatroskaIndexPos *cue = &positions[j];
            MatroskaTrack *track = matroska_find_track_by_num(m, cue->track);
            int64_t block_pos;
            uint64_t block_bytes;
            if (!track || !track->stream || track->type != MATROSKA_TRACK_TYPE_SUBTITLE ||
                track->stream->discard == AVDISCARD_ALL || !cue->duration || cue->duration > INT64_MAX ||
                !cue->relative || cue->pos > INT64_MAX - m->segment_start)
                continue;
            const int64_t duration = av_rescale(cue->duration, m->time_scale, 1000);
            const int64_t cluster_pos = m->segment_start + cue->pos;
            if (duration <= 0 || target - start >= duration || cluster_pos >= resume_pos)
                continue;
            if (attempts++ >= SW_CUE_COUNT_LIMIT || bytes >= SW_CUE_BYTE_LIMIT)
                goto restore;
            moved = 1;
            if (!sw_cue_prepare(m, cue, cluster_pos, &block_pos, &block_bytes))
                continue;
            int duplicate = 0;
            for (unsigned k = 0; k < count; k++)
                duplicate |= seen[k] == block_pos;
            if (duplicate || block_bytes > SW_CUE_BYTE_LIMIT - bytes)
                continue;
            seen[count++] = block_pos;
            bytes += block_bytes;
            matroska_clear_queue(m);
            if (retained >= SW_CUE_BYTE_LIMIT)
                goto restore;
            track->sw_preroll_remaining = SW_CUE_BYTE_LIMIT - retained;
            ret = matroska_parse_cluster(m);
            track->sw_preroll_remaining = 0;
            if (ret == AVERROR(ENOMEM))
                goto restore;
            if (ret < 0) {
                ret = 0;
                continue;
            }
            ret = 0;
            while (avpriv_packet_list_get(&m->queue, &packet) >= 0) {
                if (packet.stream_index == track->stream->index && packet.pts != AV_NOPTS_VALUE) {
                    const int64_t pts = av_rescale_q(packet.pts, track->stream->time_base, AV_TIME_BASE_Q);
                    const int64_t length = av_rescale_q(packet.duration, track->stream->time_base, AV_TIME_BASE_Q);
                    uint64_t packet_bytes;
                    if (pts >= 0 && pts <= target && length > 0 && target - pts < length &&
                        packets < SW_CUE_COUNT_LIMIT &&
                        sw_cue_packet_bytes(&packet, SW_CUE_BYTE_LIMIT - retained, &packet_bytes)) {
                        ret = avpriv_packet_list_put(&preroll, &packet, NULL, 0);
                        if (ret < 0)
                            goto restore;
                        packets++;
                        retained += packet_bytes;
                    }
                }
                av_packet_unref(&packet);
            }
        }
    }
restore:
    av_packet_unref(&packet);
    matroska_clear_queue(m);
    if (moved) {
        int reset = matroska_reset_status(m, 0, resume_pos);
        if (reset < 0)
            ret = reset;
    }
    m->skip_to_keyframe = old_skip;
    m->skip_to_timecode = old_time;
    m->done = 0;
    if (ret < 0)
        avpriv_packet_list_free(&preroll);
    else
        m->queue = preroll;
    return ret;
}
