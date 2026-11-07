"""Explicit, narrowly scoped derivatives of the pinned original VLC sources."""

import datetime
import re
from pathlib import Path


def pulse_version(original, profile):
    version = profile["linuxOutputScope"]["pulseVersion"]
    if (not re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?", version)
            or original["NEWS"].splitlines()[0] != ("PulseAudio " + version).encode()
            or b"'.tarball-version'" not in original["meson.build"]
            or b".tarball-version" not in original["git-version-gen"]):
        raise ValueError("Review original PulseAudio distribution-version metadata")
    # This is the original release-tarball input, absent from a Git snapshot.
    # Do not let git-version-gen invent a version from the enclosing checkout.
    return (version + "\n").encode()


def decoder_flush(original):
    """A decoder reset releases dated and undated pictures from the old input."""
    decoder = original
    for owner in [b"p_vout", b"p_owner->p_vout"]:
        before = b"vout_Flush( " + owner + b", VLC_TICK_INVALID+1 );"
        if decoder.count(before) != 1:
            raise ValueError("Review the pinned VLC decoder reset before configuring complete picture flushes")
        decoder = decoder.replace(before,
            b"/* SyncWatch modified 2026-10-30: also release undated pre-seek pictures. */\n"
            b"            vout_Flush( " + owner + b", INT64_MIN );")
    return decoder


def avformat_clock(original):
    """Sparse subtitle cues are not the audio/video clock driving slave input."""
    before = b"""        if( p_sys->tracks[i].p_es != NULL )
            i_ts_max = __MAX( i_ts_max, p_sys->tracks[i].i_pcr );"""
    after = b"""        if( p_sys->tracks[i].p_es != NULL &&
            (p_sys->ic->streams[i]->codecpar->codec_type == AVMEDIA_TYPE_AUDIO ||
             p_sys->ic->streams[i]->codecpar->codec_type == AVMEDIA_TYPE_VIDEO) )
            i_ts_max = __MAX( i_ts_max, p_sys->tracks[i].i_pcr );"""
    minimum = b"""        if( p_sys->tracks[i].p_es != NULL &&
                p_sys->tracks[i].i_pcr > VLC_TICK_INVALID &&"""
    selected = b"""        if( p_sys->tracks[i].p_es != NULL &&
                (p_sys->ic->streams[i]->codecpar->codec_type == AVMEDIA_TYPE_AUDIO ||
                 p_sys->ic->streams[i]->codecpar->codec_type == AVMEDIA_TYPE_VIDEO) &&
                p_sys->tracks[i].i_pcr > VLC_TICK_INVALID &&"""
    marker = b"    int64_t i_ts_max = INT64_MIN;"
    if any(original.count(item) != 1 for item in (before, minimum, marker)):
        raise ValueError("Review the pinned avformat clock before excluding non-media timestamps")
    return original.replace(before, after).replace(minimum, selected).replace(marker,
        b"    /* SyncWatch modified 2026-10-31: sparse subtitle cues do not drive the media clock. */\n" + marker)


def vlc_files(original, profile):
    configure = original["configure.ac"].decode()
    start = configure.index("VERSION_DATE=$(")
    end = configure.index("\n)", start) + 2
    date = datetime.datetime.fromtimestamp(profile["sourceDateEpoch"], datetime.timezone.utc).strftime("%Y-%m-%d")
    configure = configure[:start] + "dnl SyncWatch modified 2026-10-23: deterministic original source commit date.\nVERSION_DATE='" + date + "'" + configure[end:]
    player = original["lib/media_player.c"]
    old = b'    var_Create (mp, "audio-filter", VLC_VAR_STRING);'
    if player.count(old) != 1:
        raise ValueError("Review the pinned VLC audio-filter initialization before applying its configuration derivative")
    player = player.replace(old, b'    /* SyncWatch modified 2026-10-23: inherit explicitly configured player filters. */\n'
                            b'    var_Create (mp, "audio-filter", VLC_VAR_STRING | VLC_VAR_DOINHERIT);')
    plugins = original["modules/common.am"]
    old = b'AM_LDFLAGS += $(top_builddir)/modules/module.rc.lo -Wc,-static'
    if plugins.count(old) != 1:
        raise ValueError("Review the pinned Windows plugin linker profile before configuring shared codecs")
    plugins = plugins.replace(old,
        b'# SyncWatch modified 2026-10-23: shared owned codecs, static compiler support.\n'
        b'AM_LDFLAGS += $(top_builddir)/modules/module.rc.lo -Wc,-static-libgcc -Wc,-static-libstdc++')
    codecs = original["modules/codec/avcodec/fourcc.c"]
    for name, fourcc in [(b"SUBRIP", b"SUBT"), (b"ASS", b"SSA")]:
        old = b"    /* ffmpeg only: AV_CODEC_ID_" + name + b" */"
        if codecs.count(old) != 1:
            raise ValueError("Review the pinned VLC embedded subtitle codec mapping before configuring it")
        codecs = codecs.replace(old,
            b"    /* SyncWatch modified 2026-10-28: map embedded text subtitles from avformat. */\n"
            b"    { VLC_CODEC_" + fourcc + b", AV_CODEC_ID_" + name + b" },")
    return {"configure.ac": configure.encode(), "lib/media_player.c": player, "modules/common.am": plugins,
            "modules/codec/avcodec/fourcc.c": codecs,
            "modules/demux/avformat/demux.c": avformat_clock(original["modules/demux/avformat/demux.c"]),
            "src/input/decoder.c": decoder_flush(original["src/input/decoder.c"]),
            "src/revision.txt": (profile["sources"]["vlc"]["revision"] + "\n").encode()}


def ffmpeg_files(original):
    """Source-checked, disclosed Matroska subtitle preroll for a normal A/V seek."""
    source = original["libavformat/matroskadec.c"]
    replacements = [
        (b"typedef struct MatroskaTrack {\n",
         b"typedef struct MatroskaTrack {\n"
         b"    /* SyncWatch: zero for ordinary playback; negative when optional budget is exhausted. */\n"
         b"    int64_t sw_preroll_remaining;\n"),
        (b"    uint64_t pos;\n} MatroskaIndexPos;",
         b"    uint64_t pos;\n    uint64_t relative;\n    uint64_t duration;\n} MatroskaIndexPos;"),
        (b"    { MATROSKA_ID_CUERELATIVEPOSITION,EBML_NONE },",
         b"    { MATROSKA_ID_CUERELATIVEPOSITION, EBML_UINT, 0, 0, offsetof(MatroskaIndexPos, relative) },"),
        (b"    { MATROSKA_ID_CUEDURATION,        EBML_NONE },",
         b"    { MATROSKA_ID_CUEDURATION, EBML_UINT, 0, 0, offsetof(MatroskaIndexPos, duration) },"),
        (b"static int matroska_read_seek(AVFormatContext *s, int stream_index,",
         Path(__file__).with_name("preroll_limits.h").read_bytes() + b"\n" +
         Path(__file__).with_name("matroska_preroll.c").read_bytes() +
         b"\nstatic int matroska_read_seek(AVFormatContext *s, int stream_index,"),
        (b"static int matroska_parse_block(MatroskaDemuxContext *matroska, AVBufferRef *buf, uint8_t *data,",
         b"static int sw_cue_block_budget(MatroskaTrack *track, const AVBufferRef *buf, int laces,\n"
         b"                               const MatroskaBlockMore *more, int count, int64_t discard_padding);\n\n"
         b"static int matroska_parse_block(MatroskaDemuxContext *matroska, AVBufferRef *buf, uint8_t *data,"),
        (b"    trust_default_duration = track->default_duration != 0;",
         b"    /* SyncWatch: reserve all laced packet and side-data storage before parsing frames. */\n"
         b"    if (track->sw_preroll_remaining &&\n"
         b"        !sw_cue_block_budget(track, buf, laces, blockmore, nb_blockmore, discard_padding))\n"
         b"        return 0;\n\n"
         b"    trust_default_duration = track->default_duration != 0;"),
        (b"    avpriv_update_cur_dts(s, st, sti->index_entries[index].timestamp);\n    return 0;",
         b"    avpriv_update_cur_dts(s, st, sti->index_entries[index].timestamp);\n"
         b"    return sw_restore_indexed_subtitles(matroska, st, timestamp, sti->index_entries[index].pos);"),
    ]
    for before, after in replacements:
        if source.count(before) != 1:
            raise ValueError("Review the pinned FFmpeg Matroska seek before configuring subtitle preroll")
        source = source.replace(before, after)
    start = source.index(b"static int matroska_decode_buffer(")
    end = source.index(b"\nstatic void matroska_convert_tag(", start)
    decoder = source[start:end]
    bounded = [
        (b"    if (pkt_size >= 10000000U)",
         b"    /* SyncWatch: bound optional decompression before every allocation. */\n"
         b"    const int copies = track->stream && track->stream->codecpar->codec_id == AV_CODEC_ID_WEBVTT ? 2 : 1;\n"
         b"    const int max_size = track->sw_preroll_remaining ?\n"
         b"        FFMAX(0, track->sw_preroll_remaining / copies - AV_INPUT_BUFFER_PADDING_SIZE - AV_LZO_OUTPUT_PADDING) : 10000000;\n"
         b"    if ((unsigned)pkt_size >= (unsigned)max_size)"),
        (b"        pkt_size = isize + header_size;",
         b"        if (track->sw_preroll_remaining && (header_size < 0 || header_size > max_size - isize))\n"
         b"            return AVERROR_INVALIDDATA;\n"
         b"        pkt_size = isize + header_size;"),
        (b"            olen       = pkt_size *= 3;",
         b"            olen = pkt_size = track->sw_preroll_remaining ? FFMIN(pkt_size * 3, max_size) : pkt_size * 3;"),
        (b"    memset(pkt_data + pkt_size, 0, AV_INPUT_BUFFER_PADDING_SIZE);",
         b"    if (track->sw_preroll_remaining) {\n"
         b"        /* Shrink growth slack before the packet takes ownership. Laced packets share this budget. */\n"
         b"        newpktdata = av_realloc(pkt_data, pkt_size + AV_INPUT_BUFFER_PADDING_SIZE);\n"
         b"        if (!newpktdata) { result = AVERROR(ENOMEM); goto failed; }\n"
         b"        pkt_data = newpktdata;\n"
         b"        track->sw_preroll_remaining -= copies * (pkt_size + AV_INPUT_BUFFER_PADDING_SIZE);\n"
         b"        if (!track->sw_preroll_remaining) track->sw_preroll_remaining = -1;\n"
         b"    }\n"
         b"    memset(pkt_data + pkt_size, 0, AV_INPUT_BUFFER_PADDING_SIZE);"),
    ]
    for before, after in bounded:
        if decoder.count(before) != 1:
            raise ValueError("Review the pinned Matroska optional decompression budget")
        decoder = decoder.replace(before, after)
    for before, after, count in [
            (b"            pkt_size  *= 3;", b"            pkt_size = track->sw_preroll_remaining ? FFMIN(pkt_size * 3, max_size) : pkt_size * 3;", 2),
            (b"pkt_size < 10000000", b"pkt_size < max_size", 3)]:
        if decoder.count(before) != count:
            raise ValueError("Review the pinned Matroska decompression growth loops")
        decoder = decoder.replace(before, after)
    source = source[:start] + decoder + source[end:]
    return {"libavformat/matroskadec.c": source}
