#pragma once

#include "include/cef_app.h"

// This executable is a blank, capture-denying experiment. It is not a call port.
CefRefPtr<CefApp> CreateExperimentApp();
bool ConfigureExperiment(CefSettings& settings);
bool MatchesExperimentVersion();
void RequestExperimentClose();
