#pragma once

#include <optional>
#include <string_view>

inline constexpr char kLinuxKeyStatusHistogram[] =
    "OSCrypt.FreedesktopSecretKeyProvider.InitStatus";

// Decode one bounded cumulative provider-status sample. No secret data is accepted.
std::optional<int> DecodeLinuxKeyStatus(std::string_view response);
