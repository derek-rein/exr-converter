/* SPDX-License-Identifier: MIT
 *
 * Thin C ABI over the proprietary Blackmagic RAW SDK.
 *
 * Build only when you have a local copy of the official Blackmagic RAW SDK
 * (not redistributed with this repository). See docs/braw.md.
 *
 * This header is project-owned and may be redistributed with the open-source app.
 * Do NOT ship Blackmagic SDK headers, samples, or documentation.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#if defined(_WIN32)
#  if defined(BRAW_BRIDGE_EXPORTS)
#    define BRAW_BRIDGE_API __declspec(dllexport)
#  else
#    define BRAW_BRIDGE_API __declspec(dllimport)
#  endif
#else
#  define BRAW_BRIDGE_API __attribute__((visibility("default")))
#endif

/* Decode resolution ladder (maps to BlackmagicRawResolutionScale). */
enum BRAWBridgeDecodeMode {
    BRAW_DECODE_FULL = 0,
    BRAW_DECODE_HALF = 1,
    BRAW_DECODE_QUARTER = 2,
    BRAW_DECODE_EIGHTH = 3,
};

typedef struct BRAWBridgeClipInfo {
    uint32_t width;
    uint32_t height;
    uint32_t frame_count;
    float fps;
    char sdk_version[256];
    char colorspace_hint[64]; /* e.g. "ACES2065-1" (Linear + ACES AP0) */
    char camera_type[128];
} BRAWBridgeClipInfo;

/* Returns 1 if this binary was built with BRAW SDK linkage. Always 1. */
BRAW_BRIDGE_API int braw_bridge_available(void);

/* Load libBlackmagicRawAPI from *libs_path* (UTF-8 folder). Call once.
 * Returns 0 on success. */
BRAW_BRIDGE_API int braw_bridge_initialize(const char *libs_path);

BRAW_BRIDGE_API void braw_bridge_finalize(void);

BRAW_BRIDGE_API int braw_bridge_is_initialized(void);

BRAW_BRIDGE_API void braw_bridge_sdk_version(char *buf, size_t buf_len);

/* Open clip. Opaque handle. */
BRAW_BRIDGE_API void *braw_bridge_open(const char *utf8_path);

BRAW_BRIDGE_API void braw_bridge_close(void *clip);

BRAW_BRIDGE_API int braw_bridge_clip_info(void *clip, BRAWBridgeClipInfo *out);

/* Decode 0-based *frame_index* into contiguous H×W×3 float32 RGB (Linear ACES AP0).
 * Returns 0 on success. */
BRAW_BRIDGE_API int braw_bridge_decode_frame(
    void *clip,
    uint32_t frame_index,
    int decode_mode, /* BRAWBridgeDecodeMode */
    float *out_rgb_f32,
    size_t out_rgb_bytes,
    uint32_t *out_w,
    uint32_t *out_h);

BRAW_BRIDGE_API size_t braw_bridge_decode_buffer_bytes(
    void *clip, int decode_mode, uint32_t *out_w, uint32_t *out_h);

BRAW_BRIDGE_API const char *braw_bridge_last_error(void);

/* Always "cpu" for this first implementation. */
BRAW_BRIDGE_API const char *braw_bridge_decoder_kind(void);

/* Clip metadata *key* (UTF-8). Returns 1 if present, 0 if missing, -1 on error. */
BRAW_BRIDGE_API int braw_bridge_metadata_string(
    void *clip, const char *key, char *buf, size_t buf_len);

/* Timecode for 0-based *frame_index*. Returns 1 on success. */
BRAW_BRIDGE_API int braw_bridge_timecode(
    void *clip, uint32_t frame_index, char *buf, size_t buf_len);

#ifdef __cplusplus
}
#endif
