#include <iostream>
#include <string>
#include <vector>

#include "navigation_policy.h"
#if defined(OS_MAC)
#include "include/wrapper/cef_library_loader.h"
#endif

int CheckPolicy() {
  const std::vector<std::string> allowed = {
      "https://meet.google.com/",
      "https://meet.google.com/abc-defg-hij?authuser=0#fragment",
      "https://accounts.google.com/ServiceLogin?service=meet",
      "HTTPS://MEET.GOOGLE.COM:443/",
  };
  const std::vector<std::string> denied = {
      "http://meet.google.com/", "https://meet.google.com:444/",
      "https://meet.google.com.evil.example/", "https://meet.google.com@evil.example/",
      "https://someone@meet.google.com/", "https://:password@meet.google.com/",
      "https://accounts.google.com.evil.example/", "https://meet.google.com./",
      "https://google.com/", "https://127.0.0.1/", "//meet.google.com/",
      "file:///tmp/call.html", "data:text/html,test", "javascript:alert(1)",
      "chrome://settings/", "mailto:test@example.invalid", "about:blank?test",
      "", "invalid", "https://meet.google.com/" + std::string(8192, 'x'),
  };
  int failures = 0;
  for (const auto scope : {ResearchNavigationScope::Blank, ResearchNavigationScope::GoogleMeet}) {
    if (!AllowsResearchNavigation("about:blank", scope)) {
      ++failures;
    }
    for (const auto& url : allowed) {
      if (AllowsResearchNavigation(url, scope) != (scope == ResearchNavigationScope::GoogleMeet)) {
        ++failures;
      }
    }
    for (const auto& url : denied) {
      if (AllowsResearchNavigation(url, scope)) {
        ++failures;
      }
    }
  }
  std::cout << "Navigation policy failures: " << failures << '\n';
  const std::vector<std::string> meetings = {
      "https://meet.google.com/abc-defg-hij", "https://meet.google.com/zzz-zzzz-zzz",
  };
  const std::vector<std::string> invalid_entries = {
      "", "about:blank", "https://meet.google.com/landing",
      "https://accounts.google.com/abc-defg-hij", "http://meet.google.com/abc-defg-hij",
      "https://meet.google.com:443/abc-defg-hij", "HTTPS://MEET.GOOGLE.COM/abc-defg-hij",
      "https://meet.google.com/ABC-defg-hij", "https://meet.google.com/abc-de3g-hij",
      "https://meet.google.com/%61bc-defg-hij", "https://meet.google.com/abc-defg-hij/",
      "https://meet.google.com/abc-defg-hij?authuser=0",
      "https://meet.google.com/abc-defg-hij#fragment",
      "https://someone@meet.google.com/abc-defg-hij",
      "https://meet.google.com.evil.example/abc-defg-hij",
      "https://meet.google.com/abc-defg-hij\n",
  };
  int entry_failures = 0;
  for (const auto scope : {ResearchNavigationScope::Blank, ResearchNavigationScope::GoogleMeet}) {
    for (const auto& url : meetings) {
      if (AllowsResearchMeetingEntry(url, scope) != (scope == ResearchNavigationScope::GoogleMeet)) {
        ++entry_failures;
      }
    }
    for (const auto& url : invalid_entries) {
      if (AllowsResearchMeetingEntry(url, scope)) {
        ++entry_failures;
      }
    }
  }
  std::cout << "Meeting entry failures: " << entry_failures << '\n';
  failures += entry_failures;
  return failures ? 1 : 0;
}

int main(int argc, char* argv[]) {
#if defined(OS_MAC)
  if (argc != 2 || !cef_load_library(argv[1])) {
    return 2;
  }
#endif
  // CEF's public URL parser is available without starting the browser engine.
  const int result = CheckPolicy();
#if defined(OS_MAC)
  if (!cef_unload_library()) {
    return 2;
  }
#endif
  return result;
}
