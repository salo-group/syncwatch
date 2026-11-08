#include "experiment.h"

#include <cstdlib>
#include <cstdio>
#include <filesystem>
#include <memory>
#include <set>
#include <string>

#include "experiment_config.h"
#include "navigation_policy.h"
#if defined(SYNCWATCH_CEF_LINUX_KEY_PROBE)
#include "linux_key_probe.h"
#endif
#include "include/base/cef_bind.h"
#include "include/base/cef_callback.h"
#include "include/cef_browser.h"
#include "include/cef_client.h"
#include "include/cef_command_line.h"
#include "include/cef_frame.h"
#include "include/cef_task.h"
#include "include/cef_version_info.h"
#include "include/views/cef_browser_view.h"
#include "include/views/cef_window.h"
#include "include/wrapper/cef_closure_task.h"
#include "include/wrapper/cef_helpers.h"

namespace {

class Lifetime {
 public:
  CefRefPtr<CefWindow> window;
  CefRefPtr<CefBrowserView> view;
  std::set<int> browsers;
  bool window_destroyed = false;
  bool closing = false;
  bool quit = false;

  void Close() {
    CEF_REQUIRE_UI_THREAD();
    closing = true;
    if (window) {
      window->Close();
    }
  }

  void Finish() {
    CEF_REQUIRE_UI_THREAD();
    if (browsers.empty() && window_destroyed && !quit) {
      quit = true;
      CefQuitMessageLoop();
    }
  }
};

std::weak_ptr<Lifetime> active;

class BrowserClient final : public CefClient,
                            public CefLifeSpanHandler,
                            public CefRequestHandler,
                            public CefLoadHandler,
                            public CefPermissionHandler,
                            public CefDownloadHandler,
                            public CefDialogHandler {
 public:
  BrowserClient(std::weak_ptr<Lifetime> lifetime, [[maybe_unused]] const CefString& initial_url)
      : lifetime_(lifetime)
#if defined(SYNCWATCH_CEF_LINUX_KEY_PROBE)
      , verified_target_(kGoogleNavigationEnabled ? initial_url : CefString())
#endif
      {}

  CefRefPtr<CefLifeSpanHandler> GetLifeSpanHandler() override { return this; }
  CefRefPtr<CefRequestHandler> GetRequestHandler() override { return this; }
  CefRefPtr<CefLoadHandler> GetLoadHandler() override { return this; }
  CefRefPtr<CefPermissionHandler> GetPermissionHandler() override { return this; }
  CefRefPtr<CefDownloadHandler> GetDownloadHandler() override { return this; }
  CefRefPtr<CefDialogHandler> GetDialogHandler() override { return this; }

  void OnAfterCreated(CefRefPtr<CefBrowser> browser) override {
    CEF_REQUIRE_UI_THREAD();
    if (auto life = lifetime_.lock()) {
      const bool unexpected = life->closing || !life->browsers.empty();
      life->browsers.insert(browser->GetIdentifier());
      if (unexpected) {
        browser->GetHost()->CloseBrowser(true);
        return;
      }
#if defined(SYNCWATCH_CEF_LINUX_KEY_PROBE)
      key_probe_ = std::make_unique<LinuxKeyProbe>(browser, verified_target_);
#endif
    } else {
      browser->GetHost()->CloseBrowser(true);
    }
  }

  void OnBeforeClose(CefRefPtr<CefBrowser> browser) override {
    CEF_REQUIRE_UI_THREAD();
#if defined(SYNCWATCH_CEF_LINUX_KEY_PROBE)
    key_probe_.reset();
#endif
    if (auto life = lifetime_.lock()) {
      life->browsers.erase(browser->GetIdentifier());
      // Finish after CEF has returned from the final browser callback.
      CefPostTask(TID_UI, base::BindOnce([](std::weak_ptr<Lifetime> weak) {
        if (auto current = weak.lock()) {
          current->Close();
          current->Finish();
        }
      }, lifetime_));
    }
  }

  bool OnBeforePopup(CefRefPtr<CefBrowser>, CefRefPtr<CefFrame>, int,
                     const CefString&, const CefString&, cef_window_open_disposition_t,
                     bool, const CefPopupFeatures&, CefWindowInfo&,
                     CefRefPtr<CefClient>&, CefBrowserSettings&,
                     CefRefPtr<CefDictionaryValue>&, bool*) override {
    return true;
  }

  bool OnBeforeBrowse(CefRefPtr<CefBrowser>, CefRefPtr<CefFrame>,
                      CefRefPtr<CefRequest> request, bool, bool) override {
    const bool allowed = AllowsResearchNavigation(request->GetURL(), kGoogleNavigationEnabled
        ? ResearchNavigationScope::GoogleMeet : ResearchNavigationScope::Blank);
    if (!allowed) {
      std::fprintf(stderr, "CEF research document navigation rejected\n");
    }
    return !allowed;
  }

  void OnLoadEnd(CefRefPtr<CefBrowser>, CefRefPtr<CefFrame> frame, int status) override {
    if (frame->IsMain()) {
      std::fprintf(stderr, "CEF research main document HTTP status=%d\n", status);
    }
  }

  void OnLoadError(CefRefPtr<CefBrowser>, CefRefPtr<CefFrame> frame, cef_errorcode_t code,
                   const CefString&, const CefString&) override {
    if (frame->IsMain()) {
      std::fprintf(stderr, "CEF research main document load error=%d\n", static_cast<int>(code));
    }
  }

  bool OnOpenURLFromTab(CefRefPtr<CefBrowser>, CefRefPtr<CefFrame>,
                        const CefString&, cef_window_open_disposition_t, bool) override {
    return true;
  }

  bool OnRequestMediaAccessPermission(CefRefPtr<CefBrowser>, CefRefPtr<CefFrame>,
                                     const CefString&, uint32_t,
                                     CefRefPtr<CefMediaAccessCallback> callback) override {
    callback->Cancel();
    return true;
  }

  bool OnShowPermissionPrompt(CefRefPtr<CefBrowser>, uint64_t, const CefString&,
                              uint32_t, CefRefPtr<CefPermissionPromptCallback> callback) override {
    callback->Continue(CEF_PERMISSION_RESULT_DENY);
    return true;
  }

  bool CanDownload(CefRefPtr<CefBrowser>, const CefString&, const CefString&) override {
    return false;
  }

  bool OnFileDialog(CefRefPtr<CefBrowser>, FileDialogMode, const CefString&,
                    const CefString&, const std::vector<CefString>&,
                    const std::vector<CefString>&, const std::vector<CefString>&,
                    CefRefPtr<CefFileDialogCallback> callback) override {
    callback->Cancel();
    return true;
  }

  void OnRenderProcessTerminated(CefRefPtr<CefBrowser>, TerminationStatus, int,
                                 const CefString&) override {
    if (auto life = lifetime_.lock()) {
      life->Close();
    }
  }

 private:
  std::weak_ptr<Lifetime> lifetime_;
#if defined(SYNCWATCH_CEF_LINUX_KEY_PROBE)
  const CefString verified_target_;
  std::unique_ptr<LinuxKeyProbe> key_probe_;
#endif
  IMPLEMENT_REFCOUNTING(BrowserClient);
};

class SurfaceDelegate final : public CefBrowserViewDelegate {
 public:
  cef_runtime_style_t GetBrowserRuntimeStyle() override { return CEF_RUNTIME_STYLE_ALLOY; }
 private:
  IMPLEMENT_REFCOUNTING(SurfaceDelegate);
};

class WindowDelegate final : public CefWindowDelegate {
 public:
  explicit WindowDelegate(std::weak_ptr<Lifetime> lifetime) : lifetime_(lifetime) {}

  void OnWindowCreated(CefRefPtr<CefWindow> window) override {
    if (auto life = lifetime_.lock()) {
      life->window = window;
      window->SetTitle(kWindowTitle);
      window->AddChildView(life->view);
      window->Show();
      window->Activate();
    }
  }

  bool CanClose(CefRefPtr<CefWindow>) override {
    if (auto life = lifetime_.lock()) {
      life->closing = true;
      const auto browser = life->view ? life->view->GetBrowser() : nullptr;
      return !browser || browser->GetHost()->TryCloseBrowser();
    }
    return true;
  }

  void OnWindowDestroyed(CefRefPtr<CefWindow>) override {
    if (auto life = lifetime_.lock()) {
      life->window_destroyed = true;
      life->view = nullptr;
      life->window = nullptr;
      life->Finish();
    }
  }

  CefSize GetPreferredSize(CefRefPtr<CefView>) override {
    return CefSize(kInitialWidth, kInitialHeight);
  }
  CefSize GetMinimumSize(CefRefPtr<CefView>) override {
    return CefSize(kMinimumWidth, kMinimumHeight);
  }
  cef_runtime_style_t GetWindowRuntimeStyle() override { return CEF_RUNTIME_STYLE_ALLOY; }

 private:
  std::weak_ptr<Lifetime> lifetime_;
  IMPLEMENT_REFCOUNTING(WindowDelegate);
};

class ExperimentApp final : public CefApp, public CefBrowserProcessHandler {
 public:
  explicit ExperimentApp(const CefString& initial_url)
      : initial_url_(initial_url), lifetime_(std::make_shared<Lifetime>()),
        client_(new BrowserClient(lifetime_, initial_url)) {
    active = lifetime_;
  }

  CefRefPtr<CefBrowserProcessHandler> GetBrowserProcessHandler() override { return this; }
  CefRefPtr<CefClient> GetDefaultClient() override { return client_; }

  void OnBeforeCommandLineProcessing(const CefString& process_type,
                                     CefRefPtr<CefCommandLine> command) override {
#if !defined(OS_MAC)
    if (process_type.empty()) {
#if defined(SYNCWATCH_CEF_LINUX_KEY_PROBE)
      command->AppendSwitchWithValue("password-store", "gnome-libsecret");
#endif
      // Chromium enables the macOS network sandbox independently of this feature.
      std::string enabled = command->GetSwitchValue("enable-features");
      if (!enabled.empty()) {
        enabled += ",";
      }
      command->AppendSwitchWithValue("enable-features", enabled + "NetworkServiceSandbox");
    }
#endif
    // Keep normal component and certificate-data updates. Do not import a profile.
    std::string disabled = command->GetSwitchValue("disable-features");
    if (!disabled.empty()) {
      disabled += ",";
    }
    command->AppendSwitchWithValue("disable-features", disabled + kDisabledFeatures);
  }

  void OnContextInitialized() override {
    CEF_REQUIRE_UI_THREAD();
    CefBrowserSettings settings;
    CefString startup_url = initial_url_;
#if defined(SYNCWATCH_CEF_LINUX_KEY_PROBE)
    startup_url = "about:blank";
#endif
    lifetime_->view = CefBrowserView::CreateBrowserView(
        client_, startup_url, settings, nullptr, nullptr, new SurfaceDelegate);
    if (!lifetime_->view || !CefWindow::CreateTopLevelWindow(new WindowDelegate(lifetime_))) {
      lifetime_->view = nullptr;
      lifetime_->window_destroyed = true;
      lifetime_->Finish();
    }
  }

 private:
  const CefString initial_url_;
  std::shared_ptr<Lifetime> lifetime_;
  CefRefPtr<BrowserClient> client_;
  IMPLEMENT_REFCOUNTING(ExperimentApp);
};

}  // namespace

CefRefPtr<CefApp> CreateExperimentApp(const CefString& initial_url) {
  return new ExperimentApp(initial_url);
}

bool ConfigureExperiment(CefSettings& settings, CefString& initial_url) {
#if defined(OS_WIN)
  const wchar_t* meeting = _wgetenv(L"SYNCWATCH_CEF_EXPERIMENT_MEETING_URL");
  const wchar_t* value = _wgetenv(L"SYNCWATCH_CEF_EXPERIMENT_ROOT");
#else
  const char* meeting = std::getenv("SYNCWATCH_CEF_EXPERIMENT_MEETING_URL");
  const char* value = std::getenv("SYNCWATCH_CEF_EXPERIMENT_ROOT");
#endif
  initial_url = kInitialNavigation;
  if (meeting) {
    std::size_t length = 0;
    while (length <= kResearchNavigationLimit && meeting[length]) {
      ++length;
    }
    if (!length || length > kResearchNavigationLimit) {
      return false;
    }
    const CefString candidate(meeting, length);
    if (!AllowsResearchMeetingEntry(candidate, kGoogleNavigationEnabled
        ? ResearchNavigationScope::GoogleMeet : ResearchNavigationScope::Blank)) {
      return false;
    }
    initial_url = candidate;
  }
  if (!value || !*value) {
    return false;
  }
  const auto root = std::filesystem::path(value);
#if defined(SYNCWATCH_CEF_LINUX_KEY_PROBE)
  if (!ValidateLinuxKeyProbeEnvironment(root)) {
    return false;
  }
#endif
  std::error_code error;
  if (!root.is_absolute() || !std::filesystem::create_directory(root, error) || error) {
    return false;
  }
#if !defined(OS_WIN)
  std::filesystem::permissions(root, std::filesystem::perms::owner_all,
                              std::filesystem::perm_options::replace, error);
  if (error) {
    return false;
  }
#endif
  CefString(&settings.root_cache_path) = root.native();
  settings.command_line_args_disabled = true;
  settings.no_sandbox = false;
  settings.log_severity = LOGSEVERITY_DISABLE;
  return true;
}

bool MatchesExperimentVersion() {
  const int expected[] = {CEF_VERSION_MAJOR, CEF_VERSION_MINOR, CEF_VERSION_PATCH,
                         CEF_COMMIT_NUMBER, CHROME_VERSION_MAJOR, CHROME_VERSION_MINOR,
                         CHROME_VERSION_BUILD, CHROME_VERSION_PATCH};
  for (int index = 0; index < 8; ++index) {
    if (cef_version_info(index) != expected[index]) {
      return false;
    }
  }
  return true;
}

void RequestExperimentClose() {
  if (auto life = active.lock()) {
    life->Close();
  }
}
