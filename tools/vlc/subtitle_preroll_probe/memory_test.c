/* SPDX-License-Identifier: Apache-2.0 */
#include "memory.h"

#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

static void refuses_aggregate_and_preserves_realloc(size_t limit) {
    const size_t first_size = limit / 2 + limit / 16;
    sw_subtitle_memory_result result;
    assert(sw_subtitle_memory_begin(limit));
    unsigned char *first = sw_subtitle_malloc(first_size);
    assert(first);
    memset(first, 0x35, first_size);
    assert(!sw_subtitle_memory_begin(limit));
    assert(!sw_subtitle_malloc(limit / 2));
    assert(!sw_subtitle_realloc(first, limit / 2));
    for (size_t index = 0; index < first_size; index++)
        assert(first[index] == 0x35);
    assert(!sw_subtitle_memory_finish(&result));
    sw_subtitle_free(first);
    assert(sw_subtitle_memory_finish(&result));
    assert(result.refused);
    assert(result.peak_bytes > first_size && result.peak_bytes <= limit);
}

static void frees_and_reuses_the_budget(void) {
    const size_t limit = 4096;
    sw_subtitle_memory_result result;
    assert(sw_subtitle_memory_begin(limit));
    unsigned char *first = sw_subtitle_malloc(1024);
    assert(first);
    memset(first, 0x6a, 1024);
    unsigned char *grown = sw_subtitle_realloc(first, 2048);
    assert(grown);
    for (size_t index = 0; index < 1024; index++)
        assert(grown[index] == 0x6a);
    sw_subtitle_free(grown);
    void *reused = sw_subtitle_malloc(3072);
    assert(reused);
    sw_subtitle_free(reused);
    sw_subtitle_free(NULL);
    assert(sw_subtitle_memory_finish(&result));
    assert(!result.refused);
    assert(result.peak_bytes > 3072 && result.peak_bytes <= limit);
}

static void handles_alignment_and_overflow(void) {
    const size_t limit = 4096;
    sw_subtitle_memory_result result;
    assert(sw_subtitle_memory_begin(limit));
#ifndef _WIN32
    void *aligned = NULL;
    assert(sw_subtitle_posix_memalign(&aligned, 64, 96) == 0);
    assert(aligned && (uintptr_t)aligned % 64 == 0);
    void *resized = sw_subtitle_realloc(aligned, 128);
    assert(resized && (uintptr_t)resized % 64 == 0);
    sw_subtitle_free(resized);
#endif
    assert(!sw_subtitle_malloc(SIZE_MAX));
    assert(sw_subtitle_memory_finish(&result));
    assert(result.refused);
}

int main(void) {
    refuses_aggregate_and_preserves_realloc(4096);
    refuses_aggregate_and_preserves_realloc(SW_SUBTITLE_HEAP_LIMIT);
    frees_and_reuses_the_budget();
    handles_alignment_and_overflow();
    puts("Subtitle allocation budget checks passed");
    return 0;
}
