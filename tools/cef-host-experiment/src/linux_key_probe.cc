#include "linux_key_probe.h"

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <sys/stat.h>
#include <unistd.h>

#include "experiment.h"
#include "linux_key_status.h"
#include "include/base/cef_bind.h"
#include "include/base/cef_callback.h"
#include "include/cef_devtools_message_observer.h"
#include "include/cef_frame.h"
#include "include/wrapper/cef_closure_task.h"
#include "include/wrapper/cef_helpers.h"

namespace {
int exit_code = 1;
constexpr int kMaximumAttempts = 20;
constexpr int kRetryMilliseconds = 500;
constexpr int kDeadlineMilliseconds = 15000;
}  // namespace

class LinuxKeyProbe::State final : public std::enable_shared_from_this<State> {
 public:
  State(CefRefPtr<CefBrowser> browser, const CefString& verified_target)
      : browser_(browser), verified_target_(verified_target),
        deadline_(std::chrono::steady_clock::now() +
                                    std::chrono::milliseconds(kDeadlineMilliseconds)) {}

  void Start() {
    CEF_REQUIRE_UI_THREAD();
    CefPostDelayedTask(TID_UI, base::BindOnce([](std::weak_ptr<State> weak) {
      if (auto state = weak.lock()) {
        state->Finish(-1);
      }
    }, weak_from_this()), kDeadlineMilliseconds);
    Request();
  }

  void Stop() {
    CEF_REQUIRE_UI_THREAD();
    complete_ = true;
    browser_ = nullptr;
  }

  void Result(int message_id, bool success, std::optional<int> status) {
    CEF_REQUIRE_UI_THREAD();
    if (complete_ || message_id != pending_id_) {
      return;
    }
    pending_id_ = 0;
    if (std::chrono::steady_clock::now() >= deadline_) {
      Finish(-1);
      return;
    }
    if (success) {
      Finish(status.value_or(-1));
      return;
    }
    // A not-yet-recorded histogram is unavailable. Retry without logging the response.
    CefPostDelayedTask(TID_UI, base::BindOnce([](std::weak_ptr<State> weak) {
      if (auto state = weak.lock()) {
        state->Request();
      }
    }, weak_from_this()), kRetryMilliseconds);
  }

 private:
  void Request() {
    CEF_REQUIRE_UI_THREAD();
    if (complete_) {
      return;
    }
    if (attempts_ >= kMaximumAttempts || std::chrono::steady_clock::now() >= deadline_) {
      Finish(-1);
      return;
    }
    ++attempts_;
    const auto parameters = CefDictionaryValue::Create();
    parameters->SetString("name", kLinuxKeyStatusHistogram);
    parameters->SetBool("delta", false);
    pending_id_ = browser_->GetHost()->ExecuteDevToolsMethod(0, "Browser.getHistogram", parameters);
    if (!pending_id_) {
      Finish(-1);
    }
  }

  void Finish(int status) {
    CEF_REQUIRE_UI_THREAD();
    if (complete_) {
      return;
    }
    complete_ = true;
    exit_code = status == 0 ? 0 : 1;
    std::fprintf(stderr, "CEF research Linux key provider status=%d attempts=%d\n", status, attempts_);
    if (status == 0 && !verified_target_.empty()) {
      if (const auto frame = browser_->GetMainFrame()) {
        frame->LoadURL(verified_target_);
        browser_ = nullptr;
        return;
      }
      exit_code = 1;
    }
    RequestExperimentClose();
  }

  CefRefPtr<CefBrowser> browser_;
  const CefString verified_target_;
  const std::chrono::steady_clock::time_point deadline_;
  int pending_id_ = 0;
  int attempts_ = 0;
  bool complete_ = false;
};

class LinuxKeyProbe::Observer final : public CefDevToolsMessageObserver {
 public:
  explicit Observer(std::weak_ptr<State> state) : state_(state) {}

  void OnDevToolsMethodResult(CefRefPtr<CefBrowser>, int message_id, bool success,
                              const void* result, size_t size) override {
    const auto status = success && result && size <= 4096
        ? DecodeLinuxKeyStatus(std::string_view(static_cast<const char*>(result), size))
        : std::nullopt;
    // Browser-domain results can arrive before ExecuteDevToolsMethod returns.
    // Defer only bounded metadata until the assigned ID and window are ready.
    CefPostTask(TID_UI, base::BindOnce([](std::weak_ptr<State> weak, int id, bool ok,
                                       std::optional<int> sample) {
      if (auto state = weak.lock()) {
        state->Result(id, ok, sample);
      }
    }, state_, message_id, success, status));
  }

 private:
  std::weak_ptr<State> state_;
  IMPLEMENT_REFCOUNTING(Observer);
};

LinuxKeyProbe::LinuxKeyProbe(CefRefPtr<CefBrowser> browser, const CefString& verified_target)
    : state_(std::make_shared<State>(browser, verified_target)) {
  CEF_REQUIRE_UI_THREAD();
  registration_ = browser->GetHost()->AddDevToolsMessageObserver(new Observer(state_));
  state_->Start();
}

LinuxKeyProbe::~LinuxKeyProbe() {
  state_->Stop();
  registration_ = nullptr;
}

int LinuxKeyProbeExitCode() {
  return exit_code;
}

bool ValidateLinuxKeyProbeEnvironment(const std::filesystem::path& root) {
  const auto scope = root.parent_path();
  if (!root.is_absolute() || root.filename() != "cef-root") {
    return false;
  }
  for (auto path = scope; !path.empty(); path = path.parent_path()) {
    struct stat info;
    if (lstat(path.c_str(), &info) || !S_ISDIR(info.st_mode)) {
      return false;
    }
    if (path == path.root_path()) {
      break;
    }
  }
  for (const auto& path : {scope, scope / "home", scope / "run", scope / "control"}) {
    struct stat info;
    if (lstat(path.c_str(), &info) || !S_ISDIR(info.st_mode) ||
        info.st_uid != getuid() || (info.st_mode & 0777) != 0700) {
      return false;
    }
  }
  const auto expected = [scope](const char* name, const std::filesystem::path& suffix,
                                const char* prefix = "") {
    const auto value = std::getenv(name);
    return value && std::string(value) == prefix + (scope / suffix).string();
  };
  if (!expected("HOME", "home") || !expected("XDG_CONFIG_HOME", "home/.config") ||
      !expected("XDG_DATA_HOME", "home/.local/share") ||
      !expected("XDG_CACHE_HOME", "home/.cache") ||
      !expected("XDG_STATE_HOME", "home/.local/state") ||
      !expected("XDG_RUNTIME_DIR", "run") || !expected("GNOME_KEYRING_CONTROL", "control") ||
      !expected("DBUS_SESSION_BUS_ADDRESS", "run/bus", "unix:path=")) {
    return false;
  }
  struct stat bus;
  return !lstat((scope / "run/bus").c_str(), &bus) && S_ISSOCK(bus.st_mode) && bus.st_uid == getuid();
}
