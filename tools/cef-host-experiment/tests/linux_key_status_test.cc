#include <cstring>
#include <iostream>
#include <string>
#include <vector>

#include "linux_key_status.h"
#include "include/cef_api_hash.h"

int main() {
  // Select the pinned public ABI without starting a browser engine.
  const auto hash = cef_api_hash(CEF_API_VERSION, 0);
  if (!hash || std::strcmp(hash, CEF_API_HASH_PLATFORM)) {
    return 2;
  }
  const auto status = DecodeLinuxKeyStatus(
      R"({"histogram":{"name":"OSCrypt.FreedesktopSecretKeyProvider.InitStatus","sum":0,"count":1,"buckets":[{"low":0,"high":1,"count":1}]}})");
  int failures = status && *status == 0 ? 0 : 1;
  for (const auto& [response, expected] : std::vector<std::pair<std::string, int>>{
      {R"({"histogram":{"name":"OSCrypt.FreedesktopSecretKeyProvider.InitStatus","sum":6,"count":1,"buckets":[{"low":6,"high":7,"count":1}]}})", 6},
      {R"({"histogram":{"name":"OSCrypt.FreedesktopSecretKeyProvider.InitStatus","sum":21,"count":1,"buckets":[{"low":21,"high":22,"count":1}]}})", 21},
  }) {
    if (DecodeLinuxKeyStatus(response) != expected) {
      ++failures;
    }
  }
  for (const auto& response : std::vector<std::string>{
      "", "{", "null", "[]", "{}", std::string(4097, 'x'),
      R"({"histogram":{"name":"Other","sum":0,"count":1,"buckets":[{"low":0,"high":1,"count":1}]}})",
      R"({"histogram":{"name":"OSCrypt.FreedesktopSecretKeyProvider.InitStatus","sum":0,"count":0,"buckets":[]}})",
      R"({"histogram":{"name":"OSCrypt.FreedesktopSecretKeyProvider.InitStatus","sum":0,"count":2,"buckets":[{"low":0,"high":1,"count":2}]}})",
      R"({"histogram":{"name":"OSCrypt.FreedesktopSecretKeyProvider.InitStatus","sum":0,"count":1,"buckets":[{"low":0,"high":2,"count":1}]}})",
      R"({"histogram":{"name":"OSCrypt.FreedesktopSecretKeyProvider.InitStatus","sum":"0","count":1,"buckets":[{"low":0,"high":1,"count":1}]}})",
      R"({"histogram":{"name":"OSCrypt.FreedesktopSecretKeyProvider.InitStatus","sum":0,"count":1,"buckets":[{"low":0,"high":1,"count":0}]}})",
      R"({"histogram":{"name":"OSCrypt.FreedesktopSecretKeyProvider.InitStatus","sum":22,"count":1,"buckets":[{"low":22,"high":23,"count":1}]}})",
      R"({"histogram":{"name":"OSCrypt.FreedesktopSecretKeyProvider.InitStatus","sum":-1,"count":1,"buckets":[{"low":-1,"high":0,"count":1}]}})",
  }) {
    if (DecodeLinuxKeyStatus(response)) {
      ++failures;
    }
  }
  std::cout << "Linux provider-status failures: " << failures << '\n';
  return failures ? 1 : 0;
}
