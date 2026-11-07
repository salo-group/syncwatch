#include "experiment.h"

#include <cstdlib>
#include <filesystem>
#include <memory>
#include <set>
#include <string>

#include "experiment_config.h"
#include "include/base/cef_bind.h"
#include "include/base/cef_callback.h"
#include "include/cef_browser.h"
#include "include/cef_client.h"
#include "include/cef_command_line.h"
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
                            public CefPermissionHandler,
                            public CefDownloadHandler,
                            public CefDialogHandler {
 public:
  explicit BrowserClient(std::weak_ptr<Lifetime> lifetime) : lifetime_(lifetime) {}

  CefRefPtr<CefLifeSpanHandler> GetLifeSpanHandler() override { return this; }
  CefRefPtr<CefRequestHandler> GetRequestHandler() override { return this; }
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
      }
    } else {
      browser->GetHost()->CloseBrowser(true);
    }
  }

  void OnBeforeClose(CefRefPtr<CefBrowser> browser) override {
    CEF_REQUIRE_UI_THREAD();
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
                     const CefString&, const CefString&, WindowOpenDisposition,
                     bool, const CefPopupFeatures&, CefWindowInfo&,
                     CefRefPtr<CefClient>&, CefBrowserSettings&,
                     CefRefPtr<CefDictionaryValue>&, bool*) override {
    return true;
  }

  bool OnBeforeBrowse(CefRefPtr<CefBrowser>, CefRefPtr<CefFrame>,
                      CefRefPtr<CefRequest> request, bool, bool) override {
    return request->GetURL() != kInitialNavigation;
  }

  bool OnOpenURLFromTab(CefRefPtr<CefBrowser>, CefRefPtr<CefFrame>,
                        const CefString&, WindowOpenDisposition, bool) override {
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
  ExperimentApp() : lifetime_(std::make_shared<Lifetime>()), client_(new BrowserClient(lifetime_)) {
    active = lifetime_;
  }

  CefRefPtr<CefBrowserProcessHandler> GetBrowserProcessHandler() override { return this; }
  CefRefPtr<CefClient> GetDefaultClient() override { return client_; }

  void OnBeforeCommandLineProcessing(const CefString&,
                                     CefRefPtr<CefCommandLine> command) override {
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
    lifetime_->view = CefBrowserView::CreateBrowserView(
        client_, kInitialNavigation, settings, nullptr, nullptr, new SurfaceDelegate);
    if (!lifetime_->view || !CefWindow::CreateTopLevelWindow(new WindowDelegate(lifetime_))) {
      lifetime_->view = nullptr;
      lifetime_->window_destroyed = true;
      lifetime_->Finish();
    }
  }

 private:
  std::shared_ptr<Lifetime> lifetime_;
  CefRefPtr<BrowserClient> client_;
  IMPLEMENT_REFCOUNTING(ExperimentApp);
};

}  // namespace

CefRefPtr<CefApp> CreateExperimentApp() { return new ExperimentApp; }

bool ConfigureExperiment(CefSettings& settings) {
#if defined(OS_WIN)
  const wchar_t* value = _wgetenv(L"SYNCWATCH_CEF_EXPERIMENT_ROOT");
#else
  const char* value = std::getenv("SYNCWATCH_CEF_EXPERIMENT_ROOT");
#endif
  if (!value || !*value) {
    return false;
  }
  const auto root = std::filesystem::path(value);
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
