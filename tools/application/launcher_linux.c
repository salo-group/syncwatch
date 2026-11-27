#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int setting(const char *key, const char *root, const char *suffix, int optional) {
    char path[PATH_MAX];
    int length = snprintf(path, sizeof(path), "%s/%s", root, suffix);
    if (length < 0 || (size_t)length >= sizeof(path)) { errno = ENAMETOOLONG; return -1; }
    if (optional && access(path, R_OK)) return unsetenv(key);
    return setenv(key, path, 1);
}

int main(int argc, char **argv) {
    char root[PATH_MAX], libraries[6 * PATH_MAX + 140];
    ssize_t length = readlink("/proc/self/exe", root, sizeof(root) - 1);
    if (length < 0) goto failed;
    if ((size_t)length >= sizeof(root) - 1) { errno = ENAMETOOLONG; goto failed; }
    root[length] = '\0';
    char *slash = strrchr(root, '/');
    if (!slash) { errno = EINVAL; goto failed; }
    *slash = '\0';
    int size = snprintf(libraries, sizeof(libraries), "%s/Renderer/runtime:%s/Media/lib:%s/Runtime/lib:%s/Runtime/lib/server:%s/Syncplay/lib:%s/Clients/lib", root, root, root, root, root, root);
    if (size < 0 || (size_t)size >= sizeof(libraries)) { errno = ENAMETOOLONG; goto failed; }
    if (setenv("LD_LIBRARY_PATH", libraries, 1) || setting("XLOCALEDIR", root, "Renderer/share/X11/locale", 0)
        || setting("FONTCONFIG_FILE", root, "Renderer/share/fonts/fonts.conf", 0)
        || setting("FONTCONFIG_PATH", root, "Renderer/share/fonts", 0)
        || setting("XERRORDB", root, "Renderer/share/X11/XErrorDB", 1)
        || setting("XCMSDB", root, "Renderer/share/X11/Xcms.txt", 1)
        || setting("XKEYSYMDB", root, "Renderer/share/X11/XKeysymDB", 1)
        || setting("ALSA_CONFIG_DIR", root, "Runtime/share/alsa", 0)
        || setting("ALSA_CONFIG_PATH", root, "Runtime/share/alsa/alsa.conf", 0)
        || setting("ALSA_PLUGIN_DIR", root, "Runtime/lib/alsa-lib", 0)
        || setting("PULSE_CLIENTCONFIG", root, "Media/share/pulse/client.conf", 0) || chdir(root)) goto failed;
    if (unsetenv("JAVA_TOOL_OPTIONS") || unsetenv("JDK_JAVA_OPTIONS") || unsetenv("_JAVA_OPTIONS") || unsetenv("CLASSPATH")) goto failed;
    char **arguments = calloc((size_t)argc + 3, sizeof(*arguments));
    if (!arguments) goto failed;
    arguments[0] = "Runtime/bin/java";
    // An inactive XRandR output can stall Skiko's refresh-based frame limiter.
    // Keep renderer selection while allowing the compositor to pace presentation.
    arguments[1] = "-Dskiko.vsync.enabled=false";
    arguments[2] = "@launch.args";
    for (int index = 1; index < argc; ++index) arguments[index + 2] = argv[index];
    execv(arguments[0], arguments);
    free(arguments);
failed:
    perror("SyncWatch");
    return 127;
}
