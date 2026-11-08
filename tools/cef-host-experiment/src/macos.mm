#import <Cocoa/Cocoa.h>
#include "experiment.h"
#include "experiment_config.h"
#include "include/cef_application_mac.h"
#include "include/wrapper/cef_helpers.h"
#include "include/wrapper/cef_library_loader.h"

@interface SyncWatchExperimentApplication : NSApplication <CefAppProtocol> {
  BOOL handling_;
}
@end

@implementation SyncWatchExperimentApplication
- (BOOL)isHandlingSendEvent { return handling_; }
- (void)setHandlingSendEvent:(BOOL)value { handling_ = value; }
- (void)sendEvent:(NSEvent*)event {
  CefScopedSendingEvent scope;
  [super sendEvent:event];
}
- (void)terminate:(id)sender { RequestExperimentClose(); }
@end

int main(int argc, char* argv[]) {
  CefScopedLibraryLoader loader;
  if (!loader.LoadInMain()) {
    return 1;
  }
  @autoreleasepool {
    [SyncWatchExperimentApplication sharedApplication];
    if (![NSApp isKindOfClass:[SyncWatchExperimentApplication class]]) {
      return 1;
    }
    NSMenu* menu = [[NSMenu alloc] initWithTitle:@""];
    NSMenuItem* application = [[NSMenuItem alloc] initWithTitle:@"" action:nil keyEquivalent:@""];
    NSMenu* actions = [[NSMenu alloc] initWithTitle:[NSString stringWithUTF8String:kWindowTitle]];
    NSMenuItem* close = [[NSMenuItem alloc] initWithTitle:[NSString stringWithUTF8String:kCloseLabel]
                                                 action:@selector(terminate:) keyEquivalent:@"q"];
    close.target = NSApp;
    [actions addItem:close];
    application.submenu = actions;
    [menu addItem:application];
    NSApp.mainMenu = menu;
    CefSettings settings;
    if (!MatchesExperimentVersion() || !ConfigureExperiment(settings)) {
      return 1;
    }
    CefMainArgs arguments(argc, argv);
    auto app = CreateExperimentApp();
    if (!CefInitialize(arguments, settings, app, nullptr)) {
      return CefGetExitCode();
    }
    CefRunMessageLoop();
    CefShutdown();
  }
  return 0;
}
