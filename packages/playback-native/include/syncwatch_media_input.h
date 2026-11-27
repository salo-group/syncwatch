/* SPDX-License-Identifier: Apache-2.0 */
#ifndef SYNCWATCH_MEDIA_INPUT_H
#define SYNCWATCH_MEDIA_INPUT_H
#include <stddef.h>
#include <stdint.h>
#ifdef _WIN32
#define SW_INPUT_API __declspec(dllexport)
#else
#define SW_INPUT_API __attribute__((visibility("default")))
#endif
#ifdef __cplusplus
extern "C" {
#endif

typedef struct sw_media_input sw_media_input;
enum sw_input_result { SW_INPUT_OK, SW_INPUT_INVALID, SW_INPUT_UNAVAILABLE, SW_INPUT_CHANGED };
/* Local-only, opaque platform facts; never serialize or compare between hosts. */
typedef struct sw_input_version { uint64_t words[8]; } sw_input_version;

/* The caller serializes these operations and retains the input until every
 * borrower of its descriptor has stopped. No threads or callbacks are created. */
SW_INPUT_API int sw_media_input_open(const char *absolute_utf8_path, sw_media_input **);
SW_INPUT_API int sw_media_input_version(sw_media_input *, sw_input_version *);
/* Observe the validated file size and the playback descriptor's current position. */
SW_INPUT_API int sw_media_input_extent(sw_media_input *, uint64_t *size, uint64_t *position);
/* A borrowed descriptor: never seek, write or close it from a client. */
SW_INPUT_API int sw_media_input_descriptor(sw_media_input *);
/* Bounded positional reads do not change the borrowed descriptor's position. */
SW_INPUT_API int sw_media_input_read_at(sw_media_input *, uint64_t offset,
    unsigned char *buffer, size_t capacity, size_t *count);
SW_INPUT_API void sw_media_input_close(sw_media_input *);

#ifdef __cplusplus
}
#endif
#endif
