#include "SyncWatchKeychainPolicy.h"
#include <Security/SecKeychain.h>

int32_t SWKeychainDisallowInteraction(void) {
    // LAContext.interactionNotAllowed covers the data protection keychain, but
    // macOS also routes SecItem requests to the legacy file-based keychain.
    // Its compatibility API is necessary while those existing records remain
    // supported. Never toggle the policy back or change the user's keychain.
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
    OSStatus status = SecKeychainSetUserInteractionAllowed(false);
#pragma clang diagnostic pop
    return status;
}
