#include "experiment.h"
#include "include/base/cef_compiler_specific.h"

NO_STACK_PROTECTOR int main(int argc, char* argv[]) {
  CefMainArgs arguments(argc, argv);
  const int child = CefExecuteProcess(arguments, nullptr, nullptr);
  if (child >= 0) {
    return child;
  }
  CefSettings settings;
  if (!MatchesExperimentVersion() || !ConfigureExperiment(settings)) {
    return 1;
  }
  auto app = CreateExperimentApp();
  if (!CefInitialize(arguments, settings, app, nullptr)) {
    return CefGetExitCode();
  }
  CefRunMessageLoop();
  CefShutdown();
  return 0;
}
