"""Keep the configured subtitle aliases narrow, reproducible and source-disclosed."""

import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from configuration import avformat_clock, decoder_flush, vlc_files


class ConfigurationChecks(unittest.TestCase):
    def setUp(self):
        self.original = {
            "configure.ac": b"VERSION_DATE=$(git date\n)\n",
            "lib/media_player.c": b'    var_Create (mp, "audio-filter", VLC_VAR_STRING);',
            "modules/common.am": b"AM_LDFLAGS += $(top_builddir)/modules/module.rc.lo -Wc,-static",
            "modules/demux/avformat/demux.c": b"/* Original LGPL grant remains here. */\n"
                b"    int64_t i_ts_max = INT64_MIN;\n"
                b"        if( p_sys->tracks[i].p_es != NULL )\n"
                b"            i_ts_max = __MAX( i_ts_max, p_sys->tracks[i].i_pcr );\n"
                b"        if( p_sys->tracks[i].p_es != NULL &&\n"
                b"                p_sys->tracks[i].i_pcr > VLC_TICK_INVALID &&\n",
            "src/input/decoder.c": b"/* Original LGPL grant remains here. */\n"
                b"            vout_Flush( p_vout, VLC_TICK_INVALID+1 );\n"
                b"            vout_Flush( p_owner->p_vout, VLC_TICK_INVALID+1 );\n",
            "modules/codec/avcodec/fourcc.c": b"/* Original LGPL grant remains here. */\n"
                b"    { VLC_CODEC_SUBT, AV_CODEC_ID_TEXT },\n"
                b"    { VLC_CODEC_SSA, AV_CODEC_ID_SSA },\n"
                b"    /* ffmpeg only: AV_CODEC_ID_SUBRIP */\n"
                b"    /* ffmpeg only: AV_CODEC_ID_ASS */\n",
        }
        self.profile = {"sourceDateEpoch": 1, "sources": {"vlc": {"revision": "a" * 40}}}

    def test_aliases_keep_original_bytes_and_existing_reverse_mapping(self):
        before = copy.deepcopy(self.original)
        first = vlc_files(self.original, self.profile)
        self.assertEqual(first, vlc_files(self.original, self.profile))
        self.assertEqual(self.original, before)
        result = first["modules/codec/avcodec/fourcc.c"]
        self.assertTrue(result.startswith(b"/* Original LGPL grant remains here. */"))
        for pair in [b"VLC_CODEC_SUBT, AV_CODEC_ID_TEXT", b"VLC_CODEC_SSA, AV_CODEC_ID_SSA",
                     b"VLC_CODEC_SUBT, AV_CODEC_ID_SUBRIP", b"VLC_CODEC_SSA, AV_CODEC_ID_ASS"]:
            self.assertEqual(result.count(pair), 1)
        self.assertIn(b"SyncWatch modified 2026-10-28", result)

    def test_source_drift_and_reapplying_a_derivative_are_rejected(self):
        key = "modules/codec/avcodec/fourcc.c"
        for content in [self.original[key].replace(b"SUBRIP", b"UNKNOWN"),
                        self.original[key] + b"    /* ffmpeg only: AV_CODEC_ID_ASS */",
                        vlc_files(self.original, self.profile)[key]]:
            with self.subTest(content=content), self.assertRaises(ValueError):
                vlc_files(self.original | {key: content}, self.profile)

    def test_decoder_reset_includes_undated_pictures_and_retains_original_notice(self):
        original = self.original["src/input/decoder.c"]
        result = decoder_flush(original)
        self.assertTrue(result.startswith(b"/* Original LGPL grant remains here. */"))
        self.assertEqual(result.count(b", INT64_MIN );"), 2)
        self.assertNotIn(b"VLC_TICK_INVALID+1", result)
        self.assertEqual(self.original["src/input/decoder.c"], original)
        self.assertEqual(vlc_files(self.original, self.profile)["src/input/decoder.c"], result)

    def test_avformat_clock_uses_media_tracks_and_rejects_source_drift(self):
        original = self.original["modules/demux/avformat/demux.c"]
        configured = avformat_clock(original)
        self.assertTrue(configured.startswith(b"/* Original LGPL grant remains here. */"))
        self.assertEqual(configured.count(b"AVMEDIA_TYPE_AUDIO"), 2)
        self.assertEqual(configured.count(b"AVMEDIA_TYPE_VIDEO"), 2)
        self.assertEqual(vlc_files(self.original, self.profile)["modules/demux/avformat/demux.c"], configured)
        for source in [configured, original + original, original.replace(b"i_ts_max = __MAX", b"changed = __MAX")]:
            with self.subTest(source=source), self.assertRaises(ValueError):
                avformat_clock(source)

    def test_decoder_reset_drift_or_duplicate_calls_fail_closed(self):
        original = self.original["src/input/decoder.c"]
        for owner in [b"p_vout", b"p_owner->p_vout"]:
            call = b"vout_Flush( " + owner + b", VLC_TICK_INVALID+1 );"
            for content in [original.replace(call, b"changed();"), original + call]:
                with self.subTest(owner=owner, content=content), self.assertRaises(ValueError):
                    decoder_flush(content)
        with self.assertRaises(ValueError):
            decoder_flush(decoder_flush(original))


if __name__ == "__main__":
    unittest.main()
