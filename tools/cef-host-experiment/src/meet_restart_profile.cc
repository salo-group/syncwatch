#include "meet_restart_profile.h"

#include <string_view>

#if defined(_WIN32)
#include <windows.h>
#include <aclapi.h>
#include <dpapi.h>
#include <vector>
#else
#include <sys/stat.h>
#include <unistd.h>
#endif

namespace {

constexpr std::string_view kScopePrefix = "meet-restart-";
constexpr char kReady[] = ".syncwatch-restart-ready";
constexpr char kReopened[] = ".syncwatch-restart-claimed";

bool IsRealDirectory(const std::filesystem::path& path) {
#if defined(_WIN32)
  const DWORD attributes = GetFileAttributesW(path.c_str());
  return attributes != INVALID_FILE_ATTRIBUTES &&
         (attributes & FILE_ATTRIBUTE_DIRECTORY) && !(attributes & FILE_ATTRIBUTE_REPARSE_POINT);
#else
  struct stat info;
  return !lstat(path.c_str(), &info) && S_ISDIR(info.st_mode);
#endif
}

#if defined(_WIN32)
std::vector<BYTE> CurrentUser() {
  HANDLE token = nullptr;
  if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) {
    return {};
  }
  DWORD size = 0;
  GetTokenInformation(token, TokenUser, nullptr, 0, &size);
  std::vector<BYTE> user(size);
  const bool ok = size && GetTokenInformation(token, TokenUser, user.data(), size, &size);
  CloseHandle(token);
  return ok ? std::move(user) : std::vector<BYTE>();
}

bool AllowsOnlyPrivateAccess(PACL acl, PSID user) {
  if (!acl || !IsValidAcl(acl)) {
    return false;
  }
  bool inherits_user_access = false;
  for (DWORD index = 0; index < acl->AceCount; ++index) {
    void* raw = nullptr;
    if (!GetAce(acl, index, &raw) || static_cast<ACE_HEADER*>(raw)->AceType != ACCESS_ALLOWED_ACE_TYPE) {
      return false;
    }
    const auto* ace = static_cast<ACCESS_ALLOWED_ACE*>(raw);
    const auto sid = const_cast<DWORD*>(&ace->SidStart);
    if (EqualSid(user, sid)) {
      const BYTE inheritance = OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE;
      inherits_user_access |= (ace->Header.AceFlags & inheritance) == inheritance &&
                              !(ace->Header.AceFlags & (INHERIT_ONLY_ACE | NO_PROPAGATE_INHERIT_ACE)) &&
                              (ace->Mask & FILE_ALL_ACCESS) == FILE_ALL_ACCESS;
    } else if (!IsWellKnownSid(sid, WinLocalSystemSid) &&
               !IsWellKnownSid(sid, WinBuiltinAdministratorsSid)) {
      return false;
    }
  }
  return inherits_user_access;
}
#endif

bool IsPrivateDirectory(const std::filesystem::path& path, bool scope = false) {
#if defined(_WIN32)
  if (!IsRealDirectory(path)) {
    return false;
  }
  auto user = CurrentUser();
  if (user.empty()) {
    return false;
  }
  PSID owner = nullptr;
  PACL acl = nullptr;
  PSECURITY_DESCRIPTOR descriptor = nullptr;
  const DWORD result = GetNamedSecurityInfoW(path.c_str(), SE_FILE_OBJECT,
      OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION, &owner, nullptr, &acl, nullptr,
      &descriptor);
  SECURITY_DESCRIPTOR_CONTROL control = 0;
  DWORD revision = 0;
  const auto sid = reinterpret_cast<TOKEN_USER*>(user.data())->User.Sid;
  const bool ok = result == ERROR_SUCCESS && owner && EqualSid(owner, sid) &&
      GetSecurityDescriptorControl(descriptor, &control, &revision) &&
      (!scope || (control & SE_DACL_PROTECTED)) && AllowsOnlyPrivateAccess(acl, sid);
  LocalFree(descriptor);
  return ok;
#else
  static_cast<void>(scope);
  struct stat info;
  return !lstat(path.c_str(), &info) && S_ISDIR(info.st_mode) &&
         info.st_uid == geteuid() && (info.st_mode & 07777) == 0700;
#endif
}

bool CreatePrivateDirectory(const std::filesystem::path& path) {
#if defined(_WIN32)
  auto user = CurrentUser();
  if (user.empty()) {
    return false;
  }
  SECURITY_DESCRIPTOR descriptor;
  const auto sid = reinterpret_cast<TOKEN_USER*>(user.data())->User.Sid;
  if (!InitializeSecurityDescriptor(&descriptor, SECURITY_DESCRIPTOR_REVISION) ||
      !SetSecurityDescriptorOwner(&descriptor, sid, FALSE)) {
    return false;
  }
  // An elevated token can default to the Administrators group as owner.
  // Set the owner explicitly and inherit the already verified parent DACL.
  SECURITY_ATTRIBUTES attributes{sizeof(attributes), &descriptor, FALSE};
  return CreateDirectoryW(path.c_str(), &attributes) && IsPrivateDirectory(path);
#else
  return !mkdir(path.c_str(), 0700) && IsPrivateDirectory(path);
#endif
}

bool IsMissing(const std::filesystem::path& path) {
  std::error_code error;
  return std::filesystem::symlink_status(path, error).type() == std::filesystem::file_type::not_found;
}

std::string ScopeId(const std::filesystem::path& root) {
  const auto scope = root.parent_path();
  const auto id = scope.filename().u8string();
  if (id.size() != kScopePrefix.size() + 32) {
    return {};
  }
  auto name = std::string(id.begin(), id.end());
  if (!name.starts_with(kScopePrefix) ||
      name.find_first_not_of("0123456789abcdef", kScopePrefix.size()) != std::string::npos) {
    return {};
  }
  return name;
}

}  // namespace

#if defined(_WIN32)
bool CanProtectWindowsMeetRestartProfile() {
  // SSH and other non-interactive sessions may lack the desktop DPAPI key.
  BYTE marker[] = {0x53, 0x57};
  DATA_BLOB original{sizeof(marker), marker};
  DATA_BLOB encrypted{}, restored{};
  const bool ok = CryptProtectData(&original, nullptr, nullptr, nullptr, nullptr,
      CRYPTPROTECT_UI_FORBIDDEN, &encrypted) &&
      CryptUnprotectData(&encrypted, nullptr, nullptr, nullptr, nullptr,
          CRYPTPROTECT_UI_FORBIDDEN, &restored) &&
      restored.cbData == sizeof(marker) && restored.pbData[0] == marker[0] &&
      restored.pbData[1] == marker[1];
  LocalFree(restored.pbData);
  LocalFree(encrypted.pbData);
  return ok;
}
#endif

MeetRestartProfile::MeetRestartProfile(const std::filesystem::path& root, MeetRestartPhase phase)
    : root_(root), phase_(phase) {}

std::optional<MeetRestartProfile> MeetRestartProfile::Open(const std::filesystem::path& root,
                                                          MeetRestartPhase phase) {
  const auto scope = root.parent_path();
  if (!root.is_absolute() || root != root.lexically_normal() || root.filename() != "cef-root" ||
      ScopeId(root).empty()) {
    return std::nullopt;
  }
#if defined(_WIN32)
  if (GetDriveTypeW(root.root_path().c_str()) != DRIVE_FIXED) {
    return std::nullopt;
  }
#endif
  for (auto path = scope; ; path = path.parent_path()) {
    if (!IsRealDirectory(path)) {
      return std::nullopt;
    }
    if (path == path.root_path()) {
      break;
    }
  }
  if (!IsPrivateDirectory(scope, true)) {
    return std::nullopt;
  }
  if (phase == MeetRestartPhase::Create) {
    if (!IsMissing(scope / kReady) || !IsMissing(scope / kReopened) || !CreatePrivateDirectory(root)) {
      return std::nullopt;
    }
  } else if (!IsPrivateDirectory(root) || !IsPrivateDirectory(scope / kReady) ||
             !CreatePrivateDirectory(scope / kReopened)) {
    // The atomic claim rejects further or concurrent reopen attempts.
    return std::nullopt;
  }
  return MeetRestartProfile(root, phase);
}

std::string MeetRestartProfile::KeyId() const { return ScopeId(root_); }

bool MeetRestartProfile::Complete() const {
  return phase_ == MeetRestartPhase::Reopen || CreatePrivateDirectory(root_.parent_path() / kReady);
}
