#pragma once

#include <memory>
#include <filesystem>

#include "include/cef_browser.h"

// Verify the private provider before closing or opening an approved Google target.
// Destruction cancels all pending work.
class LinuxKeyProbe final {
 public:
  LinuxKeyProbe(CefRefPtr<CefBrowser> browser, const CefString& verified_target);
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
