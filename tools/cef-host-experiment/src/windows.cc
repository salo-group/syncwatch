#include <windows.h>
#include "experiment.h"
#include "include/cef_sandbox_win.h"
#include "include/cef_version_info.h"

#if !defined(CEF_USE_BOOTSTRAP)
#error This experiment requires the upstream sandbox bootstrap.
#endif

CEF_BOOTSTRAP_EXPORT int RunWinMain(HINSTANCE instance, LPWSTR, int,
                                    void* sandbox_info, cef_version_info_t*) {
  if (!sandbox_info) {
    return 1;
  }
  CefMainArgs arguments(instance);
  const int child = CefExecuteProcess(arguments, nullptr, sandbox_info);
  if (child >= 0) {
    return child;
  }
  CefSettings settings;
  CefString initial_url;
  if (!MatchesExperimentVersion() || !ConfigureExperiment(settings, initial_url)) {
    return 1;
  }
  auto app = CreateExperimentApp(initial_url);
  if (!CefInitialize(arguments, settings, app, sandbox_info)) {
    return CefGetExitCode();
  }
  CefRunMessageLoop();
  CefShutdown();
  return 0;
}
