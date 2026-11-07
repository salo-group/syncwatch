/* SPDX-License-Identifier: Apache-2.0 */
#include <Python.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>
#include "startup.h"
#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#else
#include <signal.h>
#endif

static wchar_t paths[4][SW_PYTHON_MAX_PATH_CHARS];

static int append_path(wchar_t *destination, const wchar_t *prefix, const wchar_t *suffix)
{
    size_t left = wcslen(prefix), right = wcslen(suffix);
    if (left >= SW_PYTHON_MAX_PATH_CHARS || right >= SW_PYTHON_MAX_PATH_CHARS - left) return 0;
    wmemcpy(destination, prefix, left);
    wmemcpy(destination + left, suffix, right + 1);
    return 1;
}

static int run(int argc, wchar_t **argv)
{
    const wchar_t *search[4];
    sw_python_paths config;
    int result;
    if (argc != 2 && (argc != 3 || wcscmp(argv[2], L"--admission-server") != 0)) {
        fputs("Helper arguments invalid\n", stderr); return 64;
    }
#ifdef _WIN32
    if (_setmode(_fileno(stdin), _O_BINARY) == -1 || _setmode(_fileno(stdout), _O_BINARY) == -1) return 70;
#define CORE L"/bin"
#define STDLIB L"/bin/python312.zip"
#else
#define CORE L"/lib"
#define STDLIB L"/lib/python312.zip"
#endif
    if (!append_path(paths[0], argv[1], L"/app") || !append_path(paths[1], argv[1], L"/python") ||
        !append_path(paths[2], argv[1], STDLIB) || !append_path(paths[3], argv[1], CORE)) {
        fputs("Helper path limit exceeded\n", stderr);
        return 64;
    }
    search[0] = paths[0]; search[1] = paths[1]; search[2] = paths[2]; search[3] = paths[3];
    config.home = config.prefix = config.base_prefix = config.exec_prefix = config.base_exec_prefix = argv[1];
    config.program_name = config.executable = config.base_executable = argv[0];
    config.search_path_count = 4;
    config.search_paths = search;
    if (sw_python_start(&config) != SW_PYTHON_OK) return 70;
    if (argc == 3) result = PyRun_SimpleString("from syncwatch_bridge.server_main import main\nmain()\n");
    else result = PyRun_SimpleString("from syncwatch_bridge.main import main\nmain()\n");
    if (sw_python_stop() != SW_PYTHON_OK) return 70;
    return result == 0 ? 0 : 70;
}

#ifdef _WIN32
int wmain(int argc, wchar_t **argv) { return run(argc, argv); }
#else
/* Decode the OS UTF-8 arguments independently of ambient locale/PYTHONUTF8. */
static wchar_t *utf8(const char *input)
{
    const unsigned char *p = (const unsigned char *)input;
    size_t length = 0;
    wchar_t *output = calloc(SW_PYTHON_MAX_PATH_CHARS, sizeof(wchar_t));
    if (output == NULL) return NULL;
    while (*p) {
        unsigned int value = *p++, minimum = 0;
        int count = 0;
        if (value >= 0xc2 && value <= 0xdf) { count = 1; value &= 31; minimum = 0x80; }
        else if (value >= 0xe0 && value <= 0xef) { count = 2; value &= 15; minimum = 0x800; }
        else if (value >= 0xf0 && value <= 0xf4) { count = 3; value &= 7; minimum = 0x10000; }
        else if (value >= 0x80) goto invalid;
        while (count--) {
            if ((*p & 0xc0) != 0x80) goto invalid;
            value = (value << 6) | (*p++ & 63);
        }
        if (value < minimum || value > 0x10ffff || (value >= 0xd800 && value <= 0xdfff) ||
            length >= SW_PYTHON_MAX_PATH_CHARS - 1) goto invalid;
        output[length++] = (wchar_t)value;
    }
    return output;
invalid:
    free(output);
    return NULL;
}

int main(int argc, char **argv)
{
    wchar_t *arguments[3] = {NULL, NULL, NULL};
    int result;
    if (argc != 2 && argc != 3) { fputs("Helper argument count invalid\n", stderr); return 64; }
    /* Isolated helper policy: a closed socket/pipe reports an I/O error to
       CPython/Twisted instead of terminating the process during recovery. */
    if (signal(SIGPIPE, SIG_IGN) == SIG_ERR) return 70;
    arguments[0] = utf8(argv[0]); arguments[1] = utf8(argv[1]);
    if (argc == 3) arguments[2] = utf8(argv[2]);
    if (arguments[0] == NULL || arguments[1] == NULL || (argc == 3 && arguments[2] == NULL)) { fputs("Helper path encoding invalid\n", stderr); result = 64; }
    else result = run(argc, arguments);
    free(arguments[0]); free(arguments[1]); free(arguments[2]);
    return result;
}
#endif
