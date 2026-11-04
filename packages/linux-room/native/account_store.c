/* SPDX-License-Identifier: Apache-2.0 */
/* Secret Service adapter. Never puts a secret in argv, attributes, files, or diagnostics. */
#define _GNU_SOURCE
#define _POSIX_C_SOURCE 200809L
#include <dbus/dbus.h>
#include <dlfcn.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

enum { LIMIT = 2048, UNAVAILABLE = 3, INVALID = 4 };
static const char *service = "org.freedesktop.secrets";
static const char *root = "/org/freedesktop/secrets";
static const char *service_interface = "org.freedesktop.Secret.Service";
static const char *content_type = "application/octet-stream";

static int owned_dbus(void) {
    char executable[PATH_MAX], expected[PATH_MAX], actual[PATH_MAX], canonical[PATH_MAX];
    ssize_t size = readlink("/proc/self/exe", executable, sizeof(executable) - 1);
    if (size <= 0 || (size_t)size >= sizeof(executable) - 1) return 0;
    executable[size] = '\0';
    char *separator = strrchr(executable, '/');
    if (!separator) return 0;
    *separator = '\0';
    int length = snprintf(expected, sizeof(expected), "%s/Renderer/runtime/libdbus-1.so.3", executable);
    if (length < 0 || (size_t)length >= sizeof(expected)) return 0;
    struct stat attributes;
    Dl_info info;
    return lstat(expected, &attributes) == 0 && S_ISREG(attributes.st_mode) && realpath(expected, canonical) &&
        dladdr((void *)dbus_get_version, &info) && info.dli_fname && realpath(info.dli_fname, actual) && !strcmp(canonical, actual);
}

static DBusMessage *message(const char *path, const char *interface, const char *method) {
    return dbus_message_new_method_call(service, path, interface, method);
}
static DBusMessage *call(DBusConnection *connection, DBusMessage *request) {
    if (!request) return NULL;
    DBusError error;
    dbus_error_init(&error);
    DBusMessage *reply = dbus_connection_send_with_reply_and_block(connection, request, 2000, &error);
    if (!reply) fprintf(stderr, "Account store: Secret Service %s request failed\n", dbus_message_get_member(request));
    dbus_message_unref(request);
    dbus_error_free(&error);
    return reply;
}
static int text(DBusMessageIter *iter, int type, const char *value) {
    return dbus_message_iter_append_basic(iter, type, &value);
}
static int attribute(DBusMessageIter *array, const char *key, const char *value) {
    DBusMessageIter entry;
    return dbus_message_iter_open_container(array, DBUS_TYPE_DICT_ENTRY, NULL, &entry) &&
        text(&entry, DBUS_TYPE_STRING, key) && text(&entry, DBUS_TYPE_STRING, value) &&
        dbus_message_iter_close_container(array, &entry);
}
static int attributes(DBusMessageIter *iter, const char *scope) {
    DBusMessageIter array;
    return dbus_message_iter_open_container(iter, DBUS_TYPE_ARRAY, "{ss}", &array) &&
        attribute(&array, "application", "dev.syncwatch.desktop") && attribute(&array, "profile", scope) &&
        dbus_message_iter_close_container(iter, &array);
}
static char *object_path(DBusMessage *reply) {
    if (!reply || !dbus_message_has_signature(reply, "o")) return NULL;
    DBusMessageIter iter;
    const char *path = NULL;
    dbus_message_iter_init(reply, &iter); dbus_message_iter_get_basic(&iter, &path);
    return strcmp(path, "/") ? strdup(path) : NULL;
}
static char *open_session(DBusConnection *connection) {
    DBusMessage *request = message(root, service_interface, "OpenSession");
    if (!request) return NULL;
    DBusMessageIter iter, variant;
    dbus_message_iter_init_append(request, &iter);
    if (!text(&iter, DBUS_TYPE_STRING, "plain") ||
        !dbus_message_iter_open_container(&iter, DBUS_TYPE_VARIANT, "s", &variant) ||
        !text(&variant, DBUS_TYPE_STRING, "") || !dbus_message_iter_close_container(&iter, &variant)) {
        dbus_message_unref(request); return NULL;
    }
    DBusMessage *reply = call(connection, request);
    char *result = NULL;
    if (reply && dbus_message_has_signature(reply, "vo")) {
        const char *output = NULL, *path = NULL;
        dbus_message_iter_init(reply, &iter); dbus_message_iter_recurse(&iter, &variant);
        if (dbus_message_iter_get_arg_type(&variant) == DBUS_TYPE_STRING) {
            dbus_message_iter_get_basic(&variant, &output);
            dbus_message_iter_next(&iter); dbus_message_iter_get_basic(&iter, &path);
            if (!*output && strcmp(path, "/")) result = strdup(path);
        }
    }
    if (reply) dbus_message_unref(reply);
    return result;
}
/* Return exactly one unlocked item. Locked or ambiguous results are never silently replaced. */
static int find_item(DBusConnection *connection, const char *scope, char **item) {
    DBusMessage *request = message(root, service_interface, "SearchItems");
    if (!request) return UNAVAILABLE;
    DBusMessageIter iter, array;
    dbus_message_iter_init_append(request, &iter);
    if (!attributes(&iter, scope)) { dbus_message_unref(request); return UNAVAILABLE; }
    DBusMessage *reply = call(connection, request);
    int result = UNAVAILABLE;
    if (reply && dbus_message_has_signature(reply, "aoao")) {
        dbus_message_iter_init(reply, &iter); dbus_message_iter_recurse(&iter, &array);
        const char *path = NULL;
        if (dbus_message_iter_get_arg_type(&array) == DBUS_TYPE_OBJECT_PATH) {
            dbus_message_iter_get_basic(&array, &path);
            if (dbus_message_iter_next(&array)) goto done;
        }
        dbus_message_iter_next(&iter); dbus_message_iter_recurse(&iter, &array);
        if (dbus_message_iter_get_arg_type(&array) != DBUS_TYPE_INVALID) goto done;
        if (!path) result = 2;
        else if ((*item = strdup(path))) result = 0;
    }
done:
    if (reply) dbus_message_unref(reply);
    return result;
}
static int read_item(DBusConnection *connection, const char *item, const char *session) {
    DBusMessage *request = message(item, "org.freedesktop.Secret.Item", "GetSecret");
    if (!request) return UNAVAILABLE;
    DBusMessageIter iter, secret, array;
    dbus_message_iter_init_append(request, &iter);
    if (!text(&iter, DBUS_TYPE_OBJECT_PATH, session)) { dbus_message_unref(request); return UNAVAILABLE; }
    DBusMessage *reply = call(connection, request);
    int result = UNAVAILABLE;
    if (reply && dbus_message_has_signature(reply, "(oayays)")) {
        const char *returned_session;
        const unsigned char *data = NULL;
        int size = 0;
        dbus_message_iter_init(reply, &iter); dbus_message_iter_recurse(&iter, &secret);
        dbus_message_iter_get_basic(&secret, &returned_session);
        if (strcmp(session, returned_session)) goto done;
        dbus_message_iter_next(&secret); dbus_message_iter_recurse(&secret, &array);
        if (dbus_message_iter_get_arg_type(&array) != DBUS_TYPE_INVALID) goto done;
        dbus_message_iter_next(&secret); dbus_message_iter_recurse(&secret, &array);
        if (dbus_message_iter_get_arg_type(&array) != DBUS_TYPE_BYTE) goto done;
        dbus_message_iter_get_fixed_array(&array, &data, &size);
        /* Providers can normalize MIME metadata. This adapter stores opaque bytes;
         * the account-session owner validates the record format, just as on the other OSes. */
        if (size < 1 || size > LIMIT) goto done;
        result = fwrite(data, 1, (size_t)size, stdout) == (size_t)size && fflush(stdout) == 0 ? 0 : UNAVAILABLE;
    }
done:
    if (result != 0) fprintf(stderr, "Account store: invalid Secret Service record\n");
    if (reply) dbus_message_unref(reply);
    return result;
}
static int append_secret(DBusMessageIter *iter, const char *session, const unsigned char *data, int size) {
    DBusMessageIter secret, array;
    return dbus_message_iter_open_container(iter, DBUS_TYPE_STRUCT, NULL, &secret) &&
        text(&secret, DBUS_TYPE_OBJECT_PATH, session) &&
        dbus_message_iter_open_container(&secret, DBUS_TYPE_ARRAY, "y", &array) &&
        dbus_message_iter_close_container(&secret, &array) &&
        dbus_message_iter_open_container(&secret, DBUS_TYPE_ARRAY, "y", &array) &&
        dbus_message_iter_append_fixed_array(&array, DBUS_TYPE_BYTE, &data, size) &&
        dbus_message_iter_close_container(&secret, &array) && text(&secret, DBUS_TYPE_STRING, content_type) &&
        dbus_message_iter_close_container(iter, &secret);
}
static int update_item(DBusConnection *connection, const char *item, const char *session,
                       const unsigned char *data, int size) {
    DBusMessage *request = message(item, "org.freedesktop.Secret.Item", "SetSecret");
    if (!request) return UNAVAILABLE;
    DBusMessageIter iter;
    dbus_message_iter_init_append(request, &iter);
    if (!append_secret(&iter, session, data, size)) { dbus_message_unref(request); return UNAVAILABLE; }
    DBusMessage *reply = call(connection, request);
    int result = reply && dbus_message_has_signature(reply, "") ? 0 : UNAVAILABLE;
    if (reply) dbus_message_unref(reply);
    return result;
}
static int property(DBusMessageIter *properties, const char *name, const char *signature,
                    const char *value, const char *scope) {
    DBusMessageIter entry, variant;
    if (!dbus_message_iter_open_container(properties, DBUS_TYPE_DICT_ENTRY, NULL, &entry) ||
        !text(&entry, DBUS_TYPE_STRING, name) ||
        !dbus_message_iter_open_container(&entry, DBUS_TYPE_VARIANT, signature, &variant)) return 0;
    if (scope ? !attributes(&variant, scope) : !text(&variant, DBUS_TYPE_STRING, value)) return 0;
    return dbus_message_iter_close_container(&entry, &variant) && dbus_message_iter_close_container(properties, &entry);
}
static int write_item(DBusConnection *connection, const char *session, const char *scope,
                      const unsigned char *data, int size) {
    DBusMessage *request = message(root, service_interface, "ReadAlias");
    if (!request) return UNAVAILABLE;
    DBusMessageIter iter, properties;
    dbus_message_iter_init_append(request, &iter);
    if (!text(&iter, DBUS_TYPE_STRING, "default")) { dbus_message_unref(request); return UNAVAILABLE; }
    DBusMessage *reply = call(connection, request);
    char *collection = object_path(reply);
    if (reply) dbus_message_unref(reply);
    if (!collection) return UNAVAILABLE;
    request = message(collection, "org.freedesktop.Secret.Collection", "CreateItem"); free(collection);
    if (!request) return UNAVAILABLE;
    dbus_bool_t replace = TRUE;
    dbus_message_iter_init_append(request, &iter);
    if (!dbus_message_iter_open_container(&iter, DBUS_TYPE_ARRAY, "{sv}", &properties) ||
        !property(&properties, "org.freedesktop.Secret.Item.Label", "s", "SyncWatch device session", NULL) ||
        !property(&properties, "org.freedesktop.Secret.Item.Attributes", "a{ss}", NULL, scope) ||
        !dbus_message_iter_close_container(&iter, &properties) ||
        !append_secret(&iter, session, data, size) ||
        !dbus_message_iter_append_basic(&iter, DBUS_TYPE_BOOLEAN, &replace)) {
        dbus_message_unref(request); return UNAVAILABLE;
    }
    reply = call(connection, request);
    int result = UNAVAILABLE;
    if (reply && dbus_message_has_signature(reply, "oo")) {
        const char *item, *prompt;
        dbus_message_iter_init(reply, &iter); dbus_message_iter_get_basic(&iter, &item);
        dbus_message_iter_next(&iter); dbus_message_iter_get_basic(&iter, &prompt);
        if (strcmp(item, "/") && !strcmp(prompt, "/")) result = 0;
    }
    if (reply) dbus_message_unref(reply);
    return result;
}
int main(int argc, char **argv) {
    if (argc != 3 || strlen(argv[2]) != 64 || strspn(argv[2], "0123456789abcdef") != 64) return INVALID;
    int read = !strcmp(argv[1], "read"), write = !strcmp(argv[1], "write"), remove = !strcmp(argv[1], "remove");
    if (!read && !write && !remove) return INVALID;
    if (!owned_dbus()) return UNAVAILABLE;
    unsigned char data[LIMIT + 1] = {0};
    size_t size = write ? fread(data, 1, sizeof(data), stdin) : 0;
    if (write && (ferror(stdin) || size < 1 || size > LIMIT)) return INVALID;
    DBusError error;
    dbus_error_init(&error);
    DBusConnection *connection = dbus_bus_get_private(DBUS_BUS_SESSION, &error);
    dbus_error_free(&error);
    if (!connection) return UNAVAILABLE;
    dbus_connection_set_exit_on_disconnect(connection, FALSE);
    dbus_connection_set_max_message_size(connection, 32768);
    dbus_connection_set_max_received_size(connection, 65536);
    char *session = NULL, *item = NULL;
    int result = find_item(connection, argv[2], &item);
    if (remove && result == 2) result = 0;
    else if (remove && result == 0) {
        DBusMessage *reply = call(connection, message(item, "org.freedesktop.Secret.Item", "Delete"));
        if (!reply || !dbus_message_has_signature(reply, "o")) result = UNAVAILABLE;
        else {
            DBusMessageIter iter; const char *prompt;
            dbus_message_iter_init(reply, &iter); dbus_message_iter_get_basic(&iter, &prompt);
            result = strcmp(prompt, "/") ? UNAVAILABLE : 0;
        }
        if (reply) dbus_message_unref(reply);
    } else if ((read && result == 0) || (write && (result == 0 || result == 2))) {
        session = open_session(connection);
        result = session ? (read ? read_item(connection, item, session) : item ? update_item(connection, item, session, data, (int)size)
            : write_item(connection, session, argv[2], data, (int)size)) : UNAVAILABLE;
    }
    /* Closing the private connection also releases its service session and unpresented prompts. */
    free(session); free(item);
    dbus_connection_close(connection); dbus_connection_unref(connection);
    for (volatile unsigned char *p = data; p < data + sizeof(data); ++p) *p = 0;
    return result;
}
