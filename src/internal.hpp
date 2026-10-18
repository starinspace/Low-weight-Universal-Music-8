#pragma once
#include <cstdint>
#include <string>
#include <vector>
using Bytes = std::vector<uint8_t>;
struct Audio {
  uint32_t rate = 0;
  int ch = 0;
  std::vector<int16_t> x;
};
constexpr size_t LIMIT = 512u * 1024u * 1024u;
void need(bool, const char *);
Bytes readfile(const std::string &);
void writefile(const std::string &, const Bytes &);
Audio wav(const Bytes &);
Bytes wave(const Audio &);
Bytes encode(const Audio &, const std::string &, int, bool);
Audio decode(Bytes);
std::pair<Bytes, int> bestpack(const Bytes &, bool);
