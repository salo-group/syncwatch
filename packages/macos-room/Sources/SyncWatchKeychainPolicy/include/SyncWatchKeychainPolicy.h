#ifndef SYNCWATCH_KEYCHAIN_POLICY_H
#define SYNCWATCH_KEYCHAIN_POLICY_H

#include <stdint.h>

// SyncWatch never unlocks a keychain or asks for a password implicitly.
// This policy applies only to Keychain Services in the calling process.
int32_t SWKeychainDisallowInteraction(void);

#endif
