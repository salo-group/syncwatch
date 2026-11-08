#pragma once

#include <cstddef>
#include "include/cef_parser.h"

enum class ResearchNavigationScope { Blank, GoogleMeet };
inline constexpr std::size_t kResearchNavigationLimit = 8192;

// This policy controls document navigation. It grants no native or capture access.
bool AllowsResearchNavigation(const CefString& url, ResearchNavigationScope scope);

// Runtime entry accepts a meeting code only, without account or redirect data.
bool AllowsResearchMeetingEntry(const CefString& url, ResearchNavigationScope scope);
