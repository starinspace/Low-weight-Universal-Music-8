#include "../include/lum8_api.h"
#include "internal.hpp"
#include <algorithm>
#include <exception>
struct Handle {
  Audio audio;
  size_t pos = 0;
};
static thread_local std::string error;
extern "C" {
void *lum8_open(const char *path) {
  error.clear();
  try {
    need(path, "Null path");
    auto a = decode(readfile(path));
    return new Handle{std::move(a), 0};
  } catch (const std::exception &e) {
    error = e.what();
    return nullptr;
  }
}
const char *lum8_error() { return error.c_str(); }
uint32_t lum8_sample_rate(void *h) {
  return h ? static_cast<Handle *>(h)->audio.rate : 0;
}
uint32_t lum8_channels(void *h) {
  return h ? static_cast<Handle *>(h)->audio.ch : 0;
}
uint64_t lum8_frames(void *h) {
  return h ? static_cast<Handle *>(h)->audio.x.size() /
                 static_cast<Handle *>(h)->audio.ch
           : 0;
}
size_t lum8_read(void *h, int16_t *out, size_t frames) {
  if (!h || !out)
    return 0;
  auto &q = *static_cast<Handle *>(h);
  size_t n = std::min(frames, q.audio.x.size() / q.audio.ch - q.pos);
  std::copy_n(q.audio.x.data() + q.pos * q.audio.ch, n * q.audio.ch, out);
  q.pos += n;
  return n;
}
int lum8_seek(void *h, uint64_t frame) {
  if (!h || frame > lum8_frames(h))
    return 0;
  static_cast<Handle *>(h)->pos = size_t(frame);
  return 1;
}
void lum8_close(void *h) { delete static_cast<Handle *>(h); }
int lum8_encode_wav(const char *in, const char *out, const char *preset,
                    int optimize) {
  error.clear();
  try {
    need(in && out && preset, "Null argument");
    auto b = encode(wav(readfile(in)), preset, 2048, optimize != 0);
    std::string name(out);
    if (name.size() >= 6 && name.substr(name.size() - 6) == ".lum8z")
      b = bestpack(b, false).first;
    writefile(out, b);
    return 1;
  } catch (const std::exception &e) {
    error = e.what();
    return 0;
  }
}
}
