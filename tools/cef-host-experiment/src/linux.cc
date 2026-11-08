#include "experiment.h"
#include "include/base/cef_compiler_specific.h"

NO_STACK_PROTECTOR int main(int argc, char* argv[]) {
  CefMainArgs arguments(argc, argv);
  const int child = CefExecuteProcess(arguments, nullptr, nullptr);
  if (child >= 0) {
    return child;
  }
  CefSettings settings;
  CefString initial_url;
  if (!MatchesExperimentVersion() || !ConfigureExperiment(settings, initial_url)) {
    return 1;
  }
  auto app = CreateExperimentApp(initial_url);
  if (!CefInitialize(arguments, settings, app, nullptr)) {
    return CefGetExitCode();
  }
  CefRunMessageLoop();
  CefShutdown();
  return 0;
}
