#include "cookie_store_probe.h"

#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <string_view>

#include "include/base/cef_bind.h"
#include "include/base/cef_callback.h"
#include "include/cef_cookie.h"
#include "include/internal/cef_time.h"
#include "include/wrapper/cef_closure_task.h"
#include "include/wrapper/cef_helpers.h"

namespace {

constexpr char kOrigin[] = "https://syncwatch-cookie-probe.invalid/";
constexpr char kName[] = "syncwatch_probe";
constexpr char kValue[] = "synthetic-restart-marker";
constexpr char kScopeMarker[] = ".syncwatch-cookie-probe";
constexpr int kTimeoutMilliseconds = 10000;
constexpr int64_t kCookieLifetimeMicroseconds = 3600LL * 1000000;
bool writing = false;
bool finished = false;
int result = 1;

void Finish(bool passed) {
  CEF_REQUIRE_UI_THREAD();
  if (finished) {
    return;
  }
  finished = true;
  result = passed ? 0 : 1;
  std::fprintf(stderr, "CEF synthetic cookie probe %s %s\n",
               writing ? "write" : "read", passed ? "passed" : "failed");
  CefPostTask(TID_UI, base::BindOnce(&CefQuitMessageLoop));
}

class ProbeCallbacks final : public CefSetCookieCallback,
                             public CefCompletionCallback,
                             public CefCookieVisitor {
 public:
  void OnComplete(bool success) override {
    if (finished) {
      return;
    }
    auto manager = CefCookieManager::GetGlobalManager(nullptr);
    if (!success || !manager || !manager->FlushStore(this)) {
      Finish(false);
    }
  }

  void OnComplete() override { Finish(true); }

  bool Visit(const CefCookie& cookie, int index, int total, bool&) override {
    Finish(index == 0 && total == 1 && cookie.secure && cookie.httponly &&
           cookie.has_expires && CefString(&cookie.name).ToString() == kName &&
           CefString(&cookie.value).ToString() == kValue);
    return false;
  }

 private:
  IMPLEMENT_REFCOUNTING(ProbeCallbacks);
};

}  // namespace

bool ConfigureCookieStoreProbe(const std::filesystem::path& root, CefSettings& settings) {
  const char* raw_mode = std::getenv("SYNCWATCH_CEF_COOKIE_PROBE_MODE");
  const std::string_view mode = raw_mode ? raw_mode : "";
  if (!root.is_absolute() || root.filename() != "cef-root" ||
      (mode != "write" && mode != "read")) {
    return false;
  }
  std::error_code error;
  if (!std::filesystem::is_directory(std::filesystem::symlink_status(root.parent_path(), error)) || error) {
    return false;
  }
  writing = mode == "write";
  const auto marker = root / kScopeMarker;
  if (writing) {
    if (!std::filesystem::create_directory(root, error) || error) {
      return false;
    }
#if !defined(OS_WIN)
    std::filesystem::permissions(root, std::filesystem::perms::owner_all,
                                 std::filesystem::perm_options::replace, error);
    if (error) {
      return false;
    }
#endif
    std::ofstream file(marker);
    file.close();
    if (!file) {
      return false;
    }
  } else if (!std::filesystem::is_directory(std::filesystem::symlink_status(root, error)) || error ||
             !std::filesystem::is_regular_file(std::filesystem::symlink_status(marker, error)) || error) {
    return false;
  }
  CefString(&settings.root_cache_path) = root.native();
  CefString(&settings.cache_path) = root.native();
  return true;
}

void StartCookieStoreProbe() {
  CEF_REQUIRE_UI_THREAD();
  CefPostDelayedTask(TID_UI, base::BindOnce(&Finish, false), kTimeoutMilliseconds);
  auto manager = CefCookieManager::GetGlobalManager(nullptr);
  if (!manager) {
    Finish(false);
    return;
  }
  CefRefPtr<ProbeCallbacks> callbacks = new ProbeCallbacks;
  if (!writing) {
    if (!manager->VisitUrlCookies(kOrigin, true, callbacks)) {
      Finish(false);
    }
    return;
  }
  CefCookie cookie;
  CefString(&cookie.name) = kName;
  CefString(&cookie.value) = kValue;
  CefString(&cookie.path) = "/";
  cookie.secure = true;
  cookie.httponly = true;
  cookie.has_expires = true;
  cookie.expires.val = cef_basetime_now().val + kCookieLifetimeMicroseconds;
  if (!manager->SetCookie(kOrigin, cookie, callbacks)) {
    Finish(false);
  }
}

int CookieStoreProbeExitCode() { return result; }
