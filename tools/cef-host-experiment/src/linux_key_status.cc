#include "linux_key_status.h"

#include "include/cef_parser.h"

std::optional<int> DecodeLinuxKeyStatus(std::string_view response) {
  if (response.empty() || response.size() > 4096) {
    return std::nullopt;
  }
  const auto value = CefParseJSON(response.data(), response.size(), JSON_PARSER_RFC);
  if (!value || value->GetType() != VTYPE_DICTIONARY) {
    return std::nullopt;
  }
  const auto histogram = value->GetDictionary()->GetDictionary("histogram");
  if (!histogram || histogram->GetType("name") != VTYPE_STRING ||
      histogram->GetString("name") != kLinuxKeyStatusHistogram ||
      histogram->GetType("sum") != VTYPE_INT || histogram->GetType("count") != VTYPE_INT ||
      histogram->GetInt("count") != 1) {
    return std::nullopt;
  }
  const int status = histogram->GetInt("sum");
  if (status < 0 || status > 21) {
    return std::nullopt;
  }
  const auto buckets = histogram->GetList("buckets");
  if (!buckets || buckets->GetSize() != 1) {
    return std::nullopt;
  }
  const auto bucket = buckets->GetDictionary(0);
  if (!bucket || bucket->GetType("low") != VTYPE_INT ||
      bucket->GetType("high") != VTYPE_INT || bucket->GetType("count") != VTYPE_INT ||
      bucket->GetInt("low") != status || bucket->GetInt("high") != status + 1 ||
      bucket->GetInt("count") != 1) {
    return std::nullopt;
  }
  return status;
}
