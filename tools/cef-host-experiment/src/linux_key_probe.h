#pragma once

#include <memory>
#include <filesystem>

#include "include/cef_browser.h"

// Own one fixed, blank-page diagnostic. Destruction cancels all pending work.
class LinuxKeyProbe final {
 public:
  explicit LinuxKeyProbe(CefRefPtr<CefBrowser> browser);
  ~LinuxKeyProbe();
  LinuxKeyProbe(const LinuxKeyProbe&) = delete;
  LinuxKeyProbe& operator=(const LinuxKeyProbe&) = delete;

 private:
  class State;
  class Observer;
  std::shared_ptr<State> state_;
  CefRefPtr<CefRegistration> registration_;
};

int LinuxKeyProbeExitCode();
bool ValidateLinuxKeyProbeEnvironment(const std::filesystem::path& root);
