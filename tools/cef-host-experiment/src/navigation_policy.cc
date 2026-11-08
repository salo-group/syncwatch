#include "navigation_policy.h"
#include <string_view>

bool AllowsResearchMeetingEntry(const CefString& url, ResearchNavigationScope scope) {
  constexpr std::string_view prefix = "https://meet.google.com/";
  constexpr std::size_t code_length = 12;
  if (scope != ResearchNavigationScope::GoogleMeet ||
      url.length() != prefix.size() + code_length || !AllowsResearchNavigation(url, scope)) {
    return false;
  }
  const auto text = url.ToString();
  if (text.size() != prefix.size() + code_length || !text.starts_with(prefix)) {
    return false;
  }
  const auto code = std::string_view(text).substr(prefix.size());
  for (std::size_t index = 0; index < code.size(); ++index) {
    if (index == 3 || index == 8) {
      if (code[index] != '-') {
        return false;
      }
    } else if (code[index] < 'a' || code[index] > 'z') {
      return false;
    }
  }
  return true;
}

bool AllowsResearchNavigation(const CefString& url, ResearchNavigationScope scope) {
  if (url == "about:blank") {
    return true;
  }
  if (scope != ResearchNavigationScope::GoogleMeet || url.length() > kResearchNavigationLimit) {
    return false;
  }
  CefURLParts parts;
  if (!CefParseURL(url, parts) || CefString(&parts.scheme) != "https" ||
      !CefString(&parts.username).empty() || !CefString(&parts.password).empty()) {
    return false;
  }
  const auto port = CefString(&parts.port).ToString();
  const auto host = CefString(&parts.host).ToString();
  return (port.empty() || port == "443") &&
         (host == "meet.google.com" || host == "accounts.google.com");
}
