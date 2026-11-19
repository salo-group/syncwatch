#pragma once

#include <filesystem>
#include <optional>
#include <string>

enum class MeetRestartPhase { Create, Reopen };

// One caller-owned, disposable profile. The launcher owns its final removal.
class MeetRestartProfile {
 public:
  static std::optional<MeetRestartProfile> Open(const std::filesystem::path& root,
                                                MeetRestartPhase phase);
  std::string KeyId() const;
  bool Complete() const;

 private:
  MeetRestartProfile(const std::filesystem::path& root, MeetRestartPhase phase);

  std::filesystem::path root_;
  MeetRestartPhase phase_;
};
