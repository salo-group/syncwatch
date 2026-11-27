/* SPDX-License-Identifier: Apache-2.0 */
#ifndef SYNCWATCH_SUBTITLE_PREROLL_MEMORY_H
#define SYNCWATCH_SUBTITLE_PREROLL_MEMORY_H

#include <stdbool.h>
#include <stddef.h>

enum { SW_SUBTITLE_HEAP_LIMIT = 4 * 1024 * 1024 };

typedef struct {
    size_t peak_bytes;
    bool refused;
} sw_subtitle_memory_result;

/* One request at a time in the dedicated, single-threaded demuxer. */
bool sw_subtitle_memory_begin(size_t limit);
bool sw_subtitle_memory_finish(sw_subtitle_memory_result *result);

void *sw_subtitle_malloc(size_t size);
void *sw_subtitle_realloc(void *pointer, size_t size);
void sw_subtitle_free(void *pointer);

#ifndef _WIN32
void *sw_subtitle_memalign(size_t alignment, size_t size);
int sw_subtitle_posix_memalign(void **pointer, size_t alignment, size_t size);
#endif

#endif
