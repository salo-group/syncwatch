"""Explicit, narrowly scoped derivatives of the pinned original VLC sources."""

import datetime
import re


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
            "src/input/decoder.c": decoder_flush(original["src/input/decoder.c"]),
            "src/revision.txt": (profile["sources"]["vlc"]["revision"] + "\n").encode()}
