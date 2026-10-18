#include "internal.hpp"
// LUM8 native port; original codec MIT Copyright (c) 2026 ScreamingLlama.
#include <algorithm>
#include <array>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <iterator>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>
#include <zlib.h>
#ifdef LUM8_ZSTD
#include <zstd.h>
#endif
#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif
void need(bool b, const char *s) {
  if (!b)
    throw std::runtime_error(s);
}
struct Reader {
  const Bytes &b;
  size_t p = 0;
  uint32_t get(int n) {
    need(p <= b.size() && size_t(n) <= b.size() - p, "Truncated input");
    uint32_t v = 0;
    for (int i = 0; i < n; i++)
      v |= uint32_t(b[p++]) << (8 * i);
    return v;
  }
};
void put(Bytes &b, uint32_t v, int n) {
  for (int i = 0; i < n; i++)
    b.push_back(uint8_t(v >> (8 * i)));
}
int signed16(uint32_t n) { return n < 32768 ? int(n) : int(n) - 65536; }
Bytes readfile(const std::string &p) {
  if (p == "-")
    return Bytes(std::istreambuf_iterator<char>(std::cin), {});
  std::ifstream f(p, std::ios::binary);
  need(bool(f), "Cannot open input");
  return Bytes(std::istreambuf_iterator<char>(f), {});
}
void writefile(const std::string &p, const Bytes &b) {
  if (p == "-") {
    std::cout.write((const char *)b.data(), b.size());
    need(bool(std::cout), "stdout failed");
  } else {
    std::ofstream f(p, std::ios::binary);
    f.write((const char *)b.data(), b.size());
    need(bool(f), "Output failed");
  }
}
Bytes unpack(const Bytes &b, int kind) {
#ifdef LUM8_ZSTD
  if (kind == 1) {
    auto n = ZSTD_getFrameContentSize(b.data(), b.size());
    need(n != ZSTD_CONTENTSIZE_UNKNOWN && n != ZSTD_CONTENTSIZE_ERROR &&
             n <= LIMIT,
         "Unknown/oversized zstd frame");
    Bytes o(n);
    auto r = ZSTD_decompress(o.data(), o.size(), b.data(), b.size());
    need(!ZSTD_isError(r) && r == n, "Bad zstd payload");
    return o;
  }
#else
  need(kind != 1, "Zstd support absent: rebuild with LUM8_ZSTD");
#endif
  if (kind == 0)
    return b;
  need(kind == 2, "Unknown compression");
  z_stream z{};
  need(inflateInit(&z) == Z_OK, "inflateInit failed");
  z.next_in = const_cast<Bytef *>(b.data());
  z.avail_in = uInt(b.size());
  Bytes o;
  std::array<uint8_t, 65536> tmp;
  int rc;
  do {
    z.next_out = tmp.data();
    z.avail_out = tmp.size();
    rc = inflate(&z, Z_NO_FLUSH);
    if (o.size() + tmp.size() - z.avail_out > LIMIT) {
      inflateEnd(&z);
      throw std::runtime_error("Decompression limit");
    }
    o.insert(o.end(), tmp.begin(), tmp.begin() + tmp.size() - z.avail_out);
  } while (rc == Z_OK);
  inflateEnd(&z);
  need(rc == Z_STREAM_END, "Invalid zlib stream");
  return o;
}
Bytes pack(const Bytes &b) {
  uLongf n = compressBound(b.size());
  Bytes o(n);
  need(compress2(o.data(), &n, b.data(), b.size(), 9) == Z_OK,
       "Compression failed");
  o.resize(n);
  return o;
}
// Select compression by actual byte count. All choices are existing v3 codecs.
std::pair<Bytes, int> bestpack(const Bytes &raw, bool allow_raw) {
  Bytes best = pack(raw);
  int kind = 2;
#ifdef LUM8_ZSTD
  Bytes z(ZSTD_compressBound(raw.size()));
  auto n = ZSTD_compress(z.data(), z.size(), raw.data(), raw.size(), 19);
  need(!ZSTD_isError(n), "Zstd compression failed");
  z.resize(n);
  if (z.size() < best.size()) {
    best = std::move(z);
    kind = 1;
  }
#endif
  if (allow_raw && raw.size() < best.size()) {
    best = raw;
    kind = 0;
  }
  return {std::move(best), kind};
}

Audio wav(const Bytes &b) {
  need(b.size() >= 12 && std::string(b.begin(), b.begin() + 4) == "RIFF" &&
           std::string(b.begin() + 8, b.begin() + 12) == "WAVE",
       "Expected RIFF WAV");
  Reader r{b, 12};
  Audio a;
  size_t start = 0, len = 0;
  bool fmt = false, data = false;
  while (r.p + 8 <= b.size()) {
    auto id = r.get(4), n = r.get(4);
    size_t p = r.p;
    need(n <= b.size() - p, "Truncated WAV chunk");
    if (id == 0x20746d66) {
      need(n >= 16, "Short fmt");
      need(r.get(2) == 1, "Only PCM16 WAV accepted");
      a.ch = r.get(2);
      a.rate = r.get(4);
      r.get(4);
      auto align = r.get(2);
      need(r.get(2) == 16 && align == uint32_t(a.ch * 2),
           "Only PCM16 WAV accepted");
      fmt = true;
    }
    if (id == 0x61746164) {
      start = p;
      len = n;
      data = true;
    }
    r.p = p + n + (n & 1);
  }
  need(fmt && data && a.ch > 0 && a.ch <= 255 && a.rate > 0 &&
           len % (a.ch * 2) == 0,
       "Invalid WAV metadata");
  r.p = start;
  for (size_t i = 0; i < len / 2; i++)
    a.x.push_back(int16_t(signed16(r.get(2))));
  return a;
}
Bytes wave(const Audio &a) {
  need(a.x.size() * 2 <= 0xffffffffu - 36, "WAV too large");
  Bytes b;
  for (char c : std::string("RIFF"))
    b.push_back(c);
  put(b, 36 + a.x.size() * 2, 4);
  for (char c : std::string("WAVEfmt "))
    b.push_back(c);
  put(b, 16, 4);
  put(b, 1, 2);
  put(b, a.ch, 2);
  put(b, a.rate, 4);
  put(b, a.rate * a.ch * 2, 4);
  put(b, a.ch * 2, 2);
  put(b, 16, 2);
  for (char c : std::string("data"))
    b.push_back(c);
  put(b, a.x.size() * 2, 4);
  for (auto x : a.x)
    put(b, uint16_t(x), 2);
  return b;
}
const int steps[] = {
    7,     8,     9,     10,    11,    12,    13,    14,    16,    17,
    19,    21,    23,    25,    28,    31,    34,    37,    41,    45,
    50,    55,    60,    66,    73,    80,    88,    97,    107,   118,
    130,   143,   157,   173,   190,   209,   230,   253,   279,   307,
    337,   371,   408,   449,   494,   544,   598,   658,   724,   796,
    876,   963,   1060,  1166,  1282,  1411,  1552,  1707,  1878,  2066,
    2272,  2499,  2749,  3024,  3327,  3660,  4026,  4428,  4871,  5358,
    5894,  6484,  7132,  7845,  8630,  9493,  10442, 11487, 12635, 13899,
    15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767};
struct State {
  int p, s;
};
State apply(State q, int code, int bits) {
  int sign = 1 << (bits - 1), m = code & (sign - 1), st = steps[q.s], d;
  if (bits == 4)
    d = (st >> 3) + ((m & 4) ? st : 0) + ((m & 2) ? st >> 1 : 0) +
        ((m & 1) ? st >> 2 : 0);
  else
    d = std::max(1, st * (2 * m + 1) / sign);
  q.p = std::clamp(q.p + ((code & sign) ? -d : d), -32768, 32767);
  int delta;
  if (bits == 2)
    delta = m ? 4 : -1;
  else if (bits == 3)
    delta = m < 2 ? -1 : (m == 2 ? 2 : 6);
  else if (bits == 4) {
    const int t[] = {-1, -1, -1, -1, 2, 4, 6, 8};
    delta = t[m];
  } else {
    const int t[] = {-1, -1, -1, -1, 0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 14};
    delta = t[m];
  }
  q.s = std::clamp(q.s + delta, 0, 88);
  return q;
}
int64_t sq(int x) { return int64_t(x) * x; }
int choose(State q, int target, int next, bool look, int bits) {
  int best = 0;
  int64_t cost = INT64_MAX;
  for (int c = 0; c < (1 << bits); c++) {
    State t = apply(q, c, bits);
    int64_t v = sq(target - t.p);
    if (look) {
      int64_t e = INT64_MAX;
      for (int d = 0; d < (1 << bits); d++)
        e = std::min(e, sq(next - apply(t, d, bits).p));
      v += e / 4;
    }
    if (v < cost) {
      cost = v;
      best = c;
    }
  }
  return best;
}
Bytes header(const Audio &a, int ver, int block) {
  Bytes b = {'L', 'U', 'M', '8', '!'};
  put(b, ver, 1);
  put(b, a.rate, 4);
  put(b, a.x.size() / a.ch, 4);
  put(b, a.ch, 1);
  put(b, block, 2);
  return b;
}
Bytes encode(const Audio &a, const std::string &preset, int block,
             bool optimize) {
  size_t frames = a.x.size() / a.ch;
  need(frames <= UINT32_MAX, "Too many frames");
  need(block >= 2 && block <= 65535, "Invalid block size");
  if (preset == "turbo") {
    Bytes b = header(a, 3, block);
    put(b, 100, 1);
    put(b, 3, 1);
    put(b, 2, 1);
    Bytes pcm;
    for (auto x : a.x)
      put(pcm, uint16_t(x), 2);
    put(b, crc32(0, pcm.data(), pcm.size()), 4);
    Bytes raw;
    size_t warm = std::min(size_t(3), frames) * a.ch;
    for (size_t i = 0; i < warm; i++)
      put(raw, uint16_t(a.x[i]), 2);
    std::vector<uint16_t> res;
    for (size_t i = warm; i < a.x.size(); i++)
      res.push_back(uint16_t(int(a.x[i]) - 3 * int(a.x[i - a.ch]) +
                             3 * int(a.x[i - 2 * a.ch]) -
                             int(a.x[i - 3 * a.ch])));
    for (auto v : res)
      raw.push_back(v & 255);
    for (auto v : res)
      raw.push_back(v >> 8);
    auto compressed = bestpack(raw, true);
    b[19] = uint8_t(compressed.second);
    const auto &p = compressed.first;
    b.insert(b.end(), p.begin(), p.end());
    return b;
  }
  int bits = preset == "low" ? 3 : preset == "ultra" ? 5 : 4;
  need(preset == "low" || preset == "medium" || preset == "best" ||
           preset == "ultra",
       "Presets: low medium best ultra turbo");
  bool look = preset == "best";
  Bytes b = header(a, 2, block);
  put(b, bits == 3 ? 1 : bits == 5 ? 4 : look ? 3 : 2, 1);
  put(b, bits, 1);
  put(b, 0, 2);
  for (size_t start = 0; start < frames; start += block) {
    size_t n = std::min(size_t(block), frames - start);
    std::vector<State> states;
    for (int ch = 0; ch < a.ch; ch++) {
      std::vector<int> dif;
      for (size_t i = 1; i < n; i++)
        dif.push_back(std::abs(int(a.x[(start + i) * a.ch + ch]) -
                               int(a.x[(start + i - 1) * a.ch + ch])));
      std::sort(dif.begin(), dif.end());
      int target = 7;
      if (!dif.empty()) {
        double p = (dif.size() - 1) * 0.75;
        size_t k = size_t(p);
        target = std::max(
            7, int(dif[k] +
                   (dif[std::min(k + 1, dif.size() - 1)] - dif[k]) * (p - k)));
      }
      int idx = 0;
      for (int j = 1; j < 89; j++)
        if (std::abs(steps[j] - target) < std::abs(steps[idx] - target))
          idx = j;
      int first = a.x[start * a.ch + ch];
      if (optimize) {
        const int baseline = idx;
        int64_t best = INT64_MAX;
        for (int j = 0; j < 89; j++) {
          State t{first, j};
          int64_t e = 0;
          for (size_t i = 1; i < std::min(n, size_t(33)); i++) {
            int x = a.x[(start + i) * a.ch + ch];
            int c = choose(t, x, 0, false, bits);
            t = apply(t, c, bits);
            e += sq(x - t.p);
          }
          if (e < best) {
            best = e;
            idx = j;
          }
        }
        auto block_error = [&](int initial) {
          State t{first, initial};
          int64_t error = 0;
          for (size_t i = 1; i < n; ++i) {
            int x = a.x[(start + i) * a.ch + ch];
            int next = i + 1 < n ? a.x[(start + i + 1) * a.ch + ch] : 0;
            int code = choose(t, x, next, look && i + 1 < n, bits);
            t = apply(t, code, bits);
            error += sq(x - t.p);
          }
          return error;
        };
        if (idx != baseline && block_error(idx) >= block_error(baseline))
          idx = baseline;
      }
      states.push_back({first, idx});
      put(b, uint16_t(first), 2);
      put(b, idx, 1);
      put(b, bits, 1);
    }
    uint32_t acc = 0;
    int filled = 0;
    for (size_t i = 1; i < n; i++)
      for (int ch = 0; ch < a.ch; ch++) {
        int x = a.x[(start + i) * a.ch + ch];
        int next = i + 1 < n ? a.x[(start + i + 1) * a.ch + ch] : 0;
        int c = choose(states[ch], x, next, look && i + 1 < n, bits);
        states[ch] = apply(states[ch], c, bits);
        acc |= uint32_t(c) << filled;
        filled += bits;
        while (filled >= 8) {
          b.push_back(acc & 255);
          acc >>= 8;
          filled -= 8;
        }
      }
    if (filled)
      b.push_back(acc & 255);
  }
  return b;
}
Audio decode(Bytes b) {
  need(b.size() <= LIMIT, "Input too large");
  if (b.size() >= 4 && b[0] == 0x28 && b[1] == 0xb5 && b[2] == 0x2f &&
      b[3] == 0xfd)
    b = unpack(b, 1);
  else if (b.size() >= 2 && b[0] == 0x78)
    b = unpack(b, 2);
  Reader r{b};
  need(b.size() >= 17, "Short header");
  std::string magic(b.begin(), b.begin() + 5);
  need(magic == "LUM8!" || magic == "NEON2", "Bad magic");
  r.p = 5;
  int ver = r.get(1);
  Audio a;
  a.rate = r.get(4);
  size_t frames = r.get(4);
  a.ch = r.get(1);
  int block = r.get(2);
  need(a.ch > 0 && a.rate > 0 && frames <= LIMIT / (2 * a.ch),
       "Invalid/oversized audio");
  a.x.resize(frames * a.ch);
  if (ver == 3) {
    need(r.get(1) == 100 && r.get(1) == 3, "Unsupported lossless mode");
    int comp = r.get(1);
    auto crc = r.get(4);
    Bytes raw = unpack(Bytes(b.begin() + r.p, b.end()), comp);
    need(raw.size() == a.x.size() * 2, "Bad lossless size");
    Reader p{raw};
    size_t warm = std::min(size_t(3), frames) * a.ch;
    for (size_t i = 0; i < warm; i++)
      a.x[i] = int16_t(signed16(p.get(2)));
    size_t count = a.x.size() - warm, offset = p.p;
    for (size_t k = 0; k < count; k++) {
      size_t i = warm + k;
      int pred = 3 * int(a.x[i - a.ch]) - 3 * int(a.x[i - 2 * a.ch]) +
                 int(a.x[i - 3 * a.ch]);
      uint32_t v =
          uint16_t(pred + raw[offset + k] + 256 * raw[offset + count + k]);
      a.x[i] = int16_t(signed16(v));
    }
    Bytes pcm;
    for (auto x : a.x)
      put(pcm, uint16_t(x), 2);
    need(crc32(0, pcm.data(), pcm.size()) == crc, "CRC mismatch");
    return a;
  }
  need(ver == 1 || ver == 2, "Unsupported version");
  need(block >= 2, "Invalid block size");
  if (ver == 2) {
    r.get(1);
    r.get(1);
    r.get(2);
  }
  for (size_t start = 0; start < frames; start += block) {
    size_t n = std::min(size_t(block), frames - start);
    std::vector<State> st;
    std::vector<int> bits;
    size_t total = 0;
    for (int ch = 0; ch < a.ch; ch++) {
      int first = signed16(r.get(2)), idx = r.get(1),
          nb = ver == 1 ? 4 : r.get(1);
      need(idx <= 88 && nb >= 2 && nb <= 5, "Invalid block state/bits");
      st.push_back({first, idx});
      bits.push_back(nb);
      total += (n - 1) * nb;
      a.x[start * a.ch + ch] = int16_t(first);
    }
    size_t bytes = (total + 7) / 8;
    need(bytes <= b.size() - r.p, "Truncated codes");
    size_t bit = 0;
    for (size_t i = 1; i < n; i++)
      for (int ch = 0; ch < a.ch; ch++) {
        int code = 0;
        if (ver == 1) {
          size_t k = bit / 4;
          code = (b[r.p + k / 2] >> ((k % 2) ? 0 : 4)) & 15;
          bit += 4;
        } else
          for (int k = 0; k < bits[ch]; k++, bit++)
            code |= ((b[r.p + bit / 8] >> (bit % 8)) & 1) << k;
        st[ch] = apply(st[ch], code, bits[ch]);
        a.x[(start + i) * a.ch + ch] = int16_t(st[ch].p);
      }
    r.p += bytes;
  }
  need(r.p == b.size(), "Trailing data");
  return a;
}
