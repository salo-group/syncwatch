#include <filesystem>
#include <string_view>

#include "meet_restart_profile.h"

#if defined(_WIN32)
int wmain(int argc, wchar_t* argv[]) {
#else
int main(int argc, char* argv[]) {
#endif
  if (argc != 2 && argc != 3) {
    return 2;
  }
  const auto root = std::filesystem::path(argv[1]) / "cef-root";
  if (argc == 3) {
    return MeetRestartProfile::Open(root, MeetRestartPhase::Create) ||
           MeetRestartProfile::Open(root, MeetRestartPhase::Reopen) ? 7 : 0;
  }
  if (MeetRestartProfile::Open(root, MeetRestartPhase::Reopen)) {
    return 3;
  }
  const auto first = MeetRestartProfile::Open(root, MeetRestartPhase::Create);
  if (!first || first->KeyId() != "meet-restart-0123456789abcdef0123456789abcdef") {
    return 4;
  }
  if (MeetRestartProfile::Open(root, MeetRestartPhase::Create) ||
      MeetRestartProfile::Open(root, MeetRestartPhase::Reopen) || !first->Complete()) {
    return 5;
  }
  const auto second = MeetRestartProfile::Open(root, MeetRestartPhase::Reopen);
  if (!second || second->KeyId() != first->KeyId() || !second->Complete() ||
      MeetRestartProfile::Open(root, MeetRestartPhase::Reopen)) {
    return 6;
  }
}
