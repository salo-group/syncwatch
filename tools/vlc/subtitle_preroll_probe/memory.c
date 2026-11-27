/* SPDX-License-Identifier: Apache-2.0 */
#include "memory.h"

#include <errno.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    void *base;
    size_t charge;
    size_t size;
    size_t alignment;
} allocation;

static struct {
    size_t limit;
    size_t live;
    sw_subtitle_memory_result result;
} budget;

bool sw_subtitle_memory_begin(size_t limit) {
    if (!limit || budget.limit)
        return false;
    budget.limit = limit;
    return true;
}

bool sw_subtitle_memory_finish(sw_subtitle_memory_result *result) {
    if (!budget.limit || budget.live)
        return false;
    *result = budget.result;
    budget.limit = 0;
    budget.result = (sw_subtitle_memory_result){0};
    return true;
}

static bool power_of_two(size_t alignment) {
    return alignment && !(alignment & (alignment - 1));
}

static void *allocate(size_t size, size_t alignment) {
    if (!budget.limit)
        return NULL;
    if (!power_of_two(alignment)) {
        budget.result.refused = true;
        return NULL;
    }
    if (alignment < _Alignof(max_align_t))
        alignment = _Alignof(max_align_t);
    if (!size)
        size = 1;
    const size_t overhead = sizeof(allocation) + (alignment - 1);
    const size_t remaining = budget.limit - budget.live;
    if (overhead > remaining || size > remaining - overhead) {
        budget.result.refused = true;
        return NULL;
    }
    const size_t charge = size + overhead;
    unsigned char *base = malloc(charge);
    if (!base) {
        budget.result.refused = true;
        return NULL;
    }
    unsigned char *start = base + sizeof(allocation);
    size_t padding = (alignment - (uintptr_t)start % alignment) % alignment;
    unsigned char *data = start + padding;
    allocation *header = (allocation *)(data - sizeof(allocation));
    *header = (allocation){base, charge, size, alignment};
    budget.live += charge;
    if (budget.live > budget.result.peak_bytes)
        budget.result.peak_bytes = budget.live;
    return data;
}

static allocation *allocation_of(void *data) {
    return (allocation *)((unsigned char *)data - sizeof(allocation));
}

void sw_subtitle_free(void *pointer) {
    if (!pointer)
        return;
    allocation *header = allocation_of(pointer);
    budget.live -= header->charge;
    free(header->base);
}

static void *resize(void *pointer, size_t size, size_t alignment) {
    void *replacement = allocate(size, alignment);
    if (replacement && pointer) {
        size_t old_size = allocation_of(pointer)->size;
        memcpy(replacement, pointer, old_size < size ? old_size : size);
        sw_subtitle_free(pointer);
    }
    return replacement;
}

void *sw_subtitle_malloc(size_t size) {
    return allocate(size, _Alignof(max_align_t));
}

void *sw_subtitle_realloc(void *pointer, size_t size) {
    size_t alignment = pointer ? allocation_of(pointer)->alignment : _Alignof(max_align_t);
    return resize(pointer, size, alignment);
}

#ifndef _WIN32
void *sw_subtitle_memalign(size_t alignment, size_t size) {
    return allocate(size, alignment);
}

int sw_subtitle_posix_memalign(void **pointer, size_t alignment, size_t size) {
    if (alignment < sizeof(void *) || !power_of_two(alignment))
        return EINVAL;
    void *data = allocate(size, alignment);
    if (!data)
        return ENOMEM;
    *pointer = data;
    return 0;
}
#else
/* The UCRT64 linker redirects both direct and DLL-import references. */
void *__wrap__aligned_malloc(size_t size, size_t alignment) {
    return allocate(size, alignment);
}

void *__wrap__aligned_realloc(void *pointer, size_t size, size_t alignment) {
    return resize(pointer, size, alignment);
}

void __wrap__aligned_free(void *pointer) {
    sw_subtitle_free(pointer);
}

void *(*const __wrap___imp__aligned_malloc)(size_t, size_t) = __wrap__aligned_malloc;
void *(*const __wrap___imp__aligned_realloc)(void *, size_t, size_t) = __wrap__aligned_realloc;
void (*const __wrap___imp__aligned_free)(void *) = __wrap__aligned_free;
#endif
