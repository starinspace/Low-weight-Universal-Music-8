#pragma once
#include <stddef.h>
#include <stdint.h>
#if defined(_WIN32) && defined(LUM8_BUILD_DLL)
#define LUM8_API __declspec(dllexport)
#elif defined(_WIN32)
#define LUM8_API __declspec(dllimport)
#else
#define LUM8_API
#endif
#ifdef __cplusplus
extern "C" {
#endif
/* Each handle belongs to one caller; do not access it concurrently.
   UTF-8 paths are supported on Linux. Windows uses the current code page.
   The entire file is decoded on open. Returns NULL on failure.
   Error text lives in thread-local storage until the next API call. */
LUM8_API void *lum8_open(const char *path);
LUM8_API const char *lum8_error(void);
LUM8_API uint32_t lum8_sample_rate(void *handle);
LUM8_API uint32_t lum8_channels(void *handle);
LUM8_API uint64_t lum8_frames(void *handle);
/* Read interleaved PCM16; capacity is measured in frames, not samples. */
LUM8_API size_t lum8_read(void *handle, int16_t *output,
                          size_t capacity_frames);
LUM8_API int lum8_seek(void *handle, uint64_t frame);
LUM8_API void lum8_close(void *handle);
LUM8_API int lum8_encode_wav(const char *input, const char *output,
                             const char *preset, int optimize);
#ifdef __cplusplus
}
#endif
