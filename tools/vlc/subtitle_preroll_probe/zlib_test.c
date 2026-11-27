/* SPDX-License-Identifier: Apache-2.0 */
#include "memory.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <zlib.h>

int main(void) {
    /* A standard zlib stream for the text "SyncWatch". */
    static const unsigned char compressed[] = {
        0x78, 0x9c, 0x0b, 0xae, 0xcc, 0x4b, 0x0e, 0x4f, 0x2c, 0x49, 0xce, 0x00, 0x00, 0x11, 0xd1, 0x03, 0x95
    };
    unsigned char output[32];
    sw_subtitle_memory_result result;
    z_stream stream = {0};
    assert(sw_subtitle_memory_begin(1024 * 1024));
    assert(inflateInit(&stream) == Z_OK);
    stream.next_in = (Bytef *)compressed;
    stream.avail_in = sizeof(compressed);
    stream.next_out = output;
    stream.avail_out = sizeof(output);
    assert(inflate(&stream, Z_FINISH) == Z_STREAM_END);
    assert(stream.total_out == 9 && !memcmp(output, "SyncWatch", 9));
    assert(inflateEnd(&stream) == Z_OK);
    assert(sw_subtitle_memory_finish(&result));
    assert(!result.refused && result.peak_bytes > 0);

    uLongf length = sizeof(output);
    assert(sw_subtitle_memory_begin(1024 * 1024));
    assert(uncompress(output, &length, compressed, sizeof(compressed)) == Z_OK);
    assert(length == 9 && !memcmp(output, "SyncWatch", 9));
    assert(sw_subtitle_memory_finish(&result));
    assert(!result.refused && result.peak_bytes > 0);

    stream = (z_stream){0};
    assert(sw_subtitle_memory_begin(64));
    assert(inflateInit(&stream) == Z_MEM_ERROR);
    assert(sw_subtitle_memory_finish(&result));
    assert(result.refused);
    length = sizeof(output);
    assert(sw_subtitle_memory_begin(64));
    assert(uncompress(output, &length, compressed, sizeof(compressed)) == Z_MEM_ERROR);
    assert(sw_subtitle_memory_finish(&result));
    assert(result.refused);
    puts("Subtitle inflate allocation checks passed");
    return 0;
}
