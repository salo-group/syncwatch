/* SPDX-License-Identifier: Apache-2.0 */
#define _POSIX_C_SOURCE 200809L
#ifdef __APPLE__
#define _DARWIN_C_SOURCE
#endif
#include "../include/syncwatch_media_input.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <limits.h>
#ifdef _WIN32
#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0A00
#endif
#include <windows.h>
#include <io.h>
#include <fcntl.h>
#else
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#endif

struct sw_media_input {
    int descriptor;
    int invalidated;
    sw_input_version original;
#ifdef _WIN32
    wchar_t *path;
    HANDLE reader;
#else
    char *path;
#endif
};
_Static_assert(sizeof(sw_input_version) == 64, "Local file version ABI");

#ifdef _WIN32
static wchar_t *decode_path(const char *path) {
    int length = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, path, -1, NULL, 0);
    if (length <= 0 || length > 32768) return NULL;
    wchar_t *decoded = malloc((size_t)length * sizeof(wchar_t));
    if (!decoded) return NULL;
    if (!MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, path, -1, decoded, length)) {
        free(decoded); return NULL;
    }
    /* Normalize before adding the extended-length prefix; prefixed paths do
     * not resolve dot segments or forward slashes through Win32 normalization. */
    DWORD capacity = GetFullPathNameW(decoded, 0, NULL, NULL);
    if (!capacity || capacity > 32764) { free(decoded); return NULL; }
    wchar_t *canonical = malloc(((size_t)capacity + 4) * sizeof(wchar_t));
    if (!canonical) { free(decoded); return NULL; }
    DWORD count = GetFullPathNameW(decoded, capacity, canonical + 4, NULL);
    free(decoded);
    if (!count || count >= capacity) { free(canonical); return NULL; }
    memcpy(canonical, L"\\\\?\\", 4 * sizeof(wchar_t));
    return canonical;
}
static int facts(HANDLE file, sw_input_version *version) {
    FILE_ID_INFO id;
    FILE_BASIC_INFO basic;
    LARGE_INTEGER size;
    if (GetFileType(file) != FILE_TYPE_DISK ||
        !GetFileInformationByHandleEx(file, FileIdInfo, &id, sizeof(id)) ||
        !GetFileInformationByHandleEx(file, FileBasicInfo, &basic, sizeof(basic)) ||
        !GetFileSizeEx(file, &size) || size.QuadPart < 0 ||
        (basic.FileAttributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT))) return 0;
    memset(version, 0, sizeof(*version));
    version->words[0] = id.VolumeSerialNumber;
    memcpy(&version->words[1], id.FileId.Identifier, sizeof(id.FileId.Identifier));
    version->words[3] = (uint64_t)size.QuadPart;
    version->words[4] = (uint64_t)basic.LastWriteTime.QuadPart;
    version->words[6] = (uint64_t)basic.ChangeTime.QuadPart;
    return 1;
}
static HANDLE open_file(const wchar_t *path) {
    /* Exclude existing/new writers and replacement for this input lifetime. */
    return CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING,
        FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_SEQUENTIAL_SCAN, NULL);
}
static int current(sw_media_input *input, sw_input_version *version) {
    HANDLE file = (HANDLE)_get_osfhandle(input->descriptor);
    HANDLE path = open_file(input->path);
    sw_input_version named, reader;
    int ok = path != INVALID_HANDLE_VALUE && facts(file, version) && facts(path, &named) &&
        facts(input->reader, &reader) && !memcmp(version, &named, sizeof(named)) &&
        !memcmp(version, &reader, sizeof(reader));
    if (path != INVALID_HANDLE_VALUE) CloseHandle(path);
    return ok;
}
#else
static int facts(const struct stat *value, sw_input_version *version) {
    if (!S_ISREG(value->st_mode) || value->st_size < 0) return 0;
    memset(version, 0, sizeof(*version));
    version->words[0] = (uint64_t)value->st_dev;
    version->words[1] = (uint64_t)value->st_ino;
    version->words[3] = (uint64_t)value->st_size;
#ifdef __APPLE__
    version->words[4] = (uint64_t)value->st_mtimespec.tv_sec;
    version->words[5] = (uint64_t)value->st_mtimespec.tv_nsec;
    version->words[6] = (uint64_t)value->st_ctimespec.tv_sec;
    version->words[7] = (uint64_t)value->st_ctimespec.tv_nsec;
#else
    version->words[4] = (uint64_t)value->st_mtim.tv_sec;
    version->words[5] = (uint64_t)value->st_mtim.tv_nsec;
    version->words[6] = (uint64_t)value->st_ctim.tv_sec;
    version->words[7] = (uint64_t)value->st_ctim.tv_nsec;
#endif
    return 1;
}
static int current(sw_media_input *input, sw_input_version *version) {
    struct stat descriptor, path;
    sw_input_version named;
    return fstat(input->descriptor, &descriptor) == 0 && lstat(input->path, &path) == 0 &&
        facts(&descriptor, version) && facts(&path, &named) && !memcmp(version, &named, sizeof(named));
}
#endif

int sw_media_input_open(const char *path, sw_media_input **result) {
    if (!result) return SW_INPUT_INVALID;
    *result = NULL;
    if (!path || !*path) return SW_INPUT_INVALID;
#ifdef _WIN32
    if (!((path[0] >= 'A' && path[0] <= 'Z') || (path[0] >= 'a' && path[0] <= 'z')) ||
        path[1] != ':' || (path[2] != '/' && path[2] != '\\')) return SW_INPUT_INVALID;
#else
    if (path[0] != '/') return SW_INPUT_INVALID;
#endif
    sw_media_input *input = calloc(1, sizeof(*input));
    if (!input) return SW_INPUT_UNAVAILABLE;
    input->descriptor = -1;
#ifdef _WIN32
    input->reader = INVALID_HANDLE_VALUE;
    input->path = decode_path(path);
    if (!input->path) goto fail;
    HANDLE file = open_file(input->path);
    if (file == INVALID_HANDLE_VALUE) goto fail;
    input->descriptor = _open_osfhandle((intptr_t)file, _O_RDONLY | _O_BINARY | _O_NOINHERIT);
    if (input->descriptor < 0) { CloseHandle(file); goto fail; }
    /* An independent file position keeps hashing out of VLC's CRT stream. */
    input->reader = open_file(input->path);
    if (input->reader == INVALID_HANDLE_VALUE) goto fail;
#else
    input->path = malloc(strlen(path) + 1);
    if (!input->path) goto fail;
    strcpy(input->path, path);
    input->descriptor = open(path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK);
    if (input->descriptor < 0) goto fail;
#endif
    if (!current(input, &input->original)) goto fail;
    *result = input;
    return SW_INPUT_OK;
fail:
    sw_media_input_close(input);
    return SW_INPUT_UNAVAILABLE;
}

int sw_media_input_version(sw_media_input *input, sw_input_version *version) {
    if (!version) return SW_INPUT_INVALID;
    memset(version, 0, sizeof(*version));
    if (!input) return SW_INPUT_INVALID;
    if (input->invalidated) return SW_INPUT_CHANGED;
    sw_input_version actual;
    if (!current(input, &actual) || memcmp(&actual, &input->original, sizeof(actual))) {
        input->invalidated = 1;
        return SW_INPUT_CHANGED;
    }
    *version = actual;
    return SW_INPUT_OK;
}
int sw_media_input_descriptor(sw_media_input *input) {
    sw_input_version version;
    return sw_media_input_version(input, &version) == SW_INPUT_OK ? input->descriptor : -1;
}
int sw_media_input_extent(sw_media_input *input, uint64_t *size, uint64_t *position) {
    if (!size || !position) return SW_INPUT_INVALID;
    *size = *position = 0;
    sw_input_version version;
    int status = sw_media_input_version(input, &version);
    if (status != SW_INPUT_OK) return status;
#ifdef _WIN32
    int64_t offset = _lseeki64(input->descriptor, 0, SEEK_CUR);
#else
    off_t offset = lseek(input->descriptor, 0, SEEK_CUR);
#endif
    if (offset < 0) return SW_INPUT_UNAVAILABLE;
    *size = version.words[3];
    *position = (uint64_t)offset;
    return SW_INPUT_OK;
}
int sw_media_input_read_at(sw_media_input *input, uint64_t offset,
    unsigned char *buffer, size_t capacity, size_t *count) {
    if (count) *count = 0;
    if (!count || !buffer || !capacity || capacity > 262144 || offset > INT64_MAX) return SW_INPUT_INVALID;
    sw_input_version version;
    int status = sw_media_input_version(input, &version);
    if (status != SW_INPUT_OK) return status;
    if (offset > version.words[3]) return SW_INPUT_INVALID;
    size_t size;
#ifdef _WIN32
    LARGE_INTEGER position;
    position.QuadPart = (int64_t)offset;
    DWORD received;
    if (!SetFilePointerEx(input->reader, position, NULL, FILE_BEGIN) ||
        !ReadFile(input->reader, buffer, (DWORD)capacity, &received, NULL)) return SW_INPUT_UNAVAILABLE;
    size = received;
#else
    ssize_t received;
    do { received = pread(input->descriptor, buffer, capacity, (off_t)offset); } while (received < 0 && errno == EINTR);
    if (received < 0) return SW_INPUT_UNAVAILABLE;
    size = (size_t)received;
#endif
    status = sw_media_input_version(input, &version);
    if (status == SW_INPUT_OK) *count = size;
    return status;
}
void sw_media_input_close(sw_media_input *input) {
    if (!input) return;
#ifdef _WIN32
    if (input->reader != INVALID_HANDLE_VALUE) CloseHandle(input->reader);
    if (input->descriptor >= 0) _close(input->descriptor);
#else
    if (input->descriptor >= 0) close(input->descriptor);
#endif
    free(input->path);
    free(input);
}
