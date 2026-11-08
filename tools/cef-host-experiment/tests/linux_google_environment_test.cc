#include <cstring>
#include <cstdlib>
#include <filesystem>
#include <iostream>

#include "experiment.h"
#include "include/cef_api_hash.h"

int main(int argc, char* argv[]) {
  const auto hash = cef_api_hash(CEF_API_VERSION, 0);
  if (!hash || std::strcmp(hash, CEF_API_HASH_PLATFORM) || argc != 2) {
    return 2;
  }
  const auto root = std::filesystem::path(argv[1]) / "cef-root";
  if (!root.is_absolute() || std::filesystem::exists(root)) {
    return 2;
  }
  setenv("SYNCWATCH_CEF_EXPERIMENT_ROOT", root.c_str(), 1);
  unsetenv("SYNCWATCH_CEF_EXPERIMENT_MEETING_URL");
  unsetenv("DBUS_SESSION_BUS_ADDRESS");
  CefSettings settings;
  CefString initial_url;
  // This tests startup policy only. It never calls CefInitialize.
  const bool configured = ConfigureExperiment(settings, initial_url);
  const bool created = std::filesystem::exists(root);
  const int failures = !configured && !created ? 0 : 1;
  if (created) {
    std::filesystem::remove(root);
  }
  std::cout << "Linux Google private-environment failures: " << failures << '\n';
  return failures ? 1 : 0;
}
