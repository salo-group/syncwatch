#include "include/cef_app.h"
#include "include/cef_sandbox_mac.h"
#include "include/wrapper/cef_library_loader.h"

#if !defined(CEF_USE_SANDBOX)
#error This experiment requires the upstream macOS sandbox.
#endif

int main(int argc, char* argv[]) {
  CefScopedSandboxContext sandbox;
  if (!sandbox.Initialize(argc, argv)) {
    return 1;
  }
  CefScopedLibraryLoader loader;
  if (!loader.LoadInHelper()) {
    return 1;
  }
  CefMainArgs arguments(argc, argv);
  return CefExecuteProcess(arguments, nullptr, nullptr);
}
