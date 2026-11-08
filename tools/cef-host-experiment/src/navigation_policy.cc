#include "navigation_policy.h"

bool AllowsResearchNavigation(const CefString& url, ResearchNavigationScope scope) {
  if (url == "about:blank") {
    return true;
  }
  if (scope != ResearchNavigationScope::GoogleMeet || url.length() > 8192) {
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
