/* SPDX-License-Identifier: Apache-2.0 */
#include "memory.h"

#include <stdint.h>
#include <string.h>
#include <zlib.h>

extern int ZEXPORT z_inflateInit_(z_streamp stream, const char *version, int stream_size);
extern int ZEXPORT z_inflate(z_streamp stream, int flush);
extern int ZEXPORT z_inflateEnd(z_streamp stream);
extern const char *ZEXPORT z_zlibVersion(void);
extern uLong ZEXPORT z_zlibCompileFlags(void);

static voidpf allocate_inflate(voidpf opaque, uInt count, uInt size) {
    (void)opaque;
    if (size && count > SIZE_MAX / size)
        return NULL;
    size_t bytes = (size_t)count * size;
    void *data = sw_subtitle_malloc(bytes);
    if (data)
        memset(data, 0, bytes);
    return data;
}

static void free_inflate(voidpf opaque, voidpf data) {
    (void)opaque;
    sw_subtitle_free(data);
}

int ZEXPORT inflateInit_(z_streamp stream, const char *version, int stream_size) {
    if (!stream || stream_size != (int)sizeof(*stream))
        return Z_STREAM_ERROR;
    stream->zalloc = allocate_inflate;
    stream->zfree = free_inflate;
    stream->opaque = NULL;
    return z_inflateInit_(stream, version, stream_size);
}

int ZEXPORT inflate(z_streamp stream, int flush) {
    return z_inflate(stream, flush);
}

int ZEXPORT inflateEnd(z_streamp stream) {
    return z_inflateEnd(stream);
}

const char *ZEXPORT zlibVersion(void) {
    return z_zlibVersion();
}

uLong ZEXPORT zlibCompileFlags(void) {
    return z_zlibCompileFlags();
}
