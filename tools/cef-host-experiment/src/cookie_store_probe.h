#pragma once

#include <filesystem>

#include "include/cef_app.h"

bool ConfigureCookieStoreProbe(const std::filesystem::path& root, CefSettings& settings);
void StartCookieStoreProbe();
int CookieStoreProbeExitCode();
