#pragma once

#include "include/cef_app.h"

// This capture-denying research executable is not a product call port.
CefRefPtr<CefApp> CreateExperimentApp(const CefString& initial_url);
bool ConfigureExperiment(CefSettings& settings, CefString& initial_url);
bool MatchesExperimentVersion();
void RequestExperimentClose();
