#pragma once

#include "include/cef_parser.h"

enum class ResearchNavigationScope { Blank, GoogleMeet };

// This policy controls document navigation. It grants no native or capture access.
bool AllowsResearchNavigation(const CefString& url, ResearchNavigationScope scope);
