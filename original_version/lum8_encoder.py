#!/usr/bin/env python3
"""LUM8 encoder: WAV/FLAC/AIFF -> .lum8 / .lum8z

Presets:
  xlow   experimental fast block-VBR, 2-4 bit
  low    fixed 3-bit, small and fast
  medium fixed 4-bit, fast greedy
  best   fixed 4-bit, current high-quality lookahead mode
  ultra  fixed 5-bit, larger but closer to original
  turbo  exact lossless delta3 byteplane + zstd/zlib, no FLAC

LUM8 = Low-weight-Universal-Music-8
Alias: NEON / NEON2
"""

import argparse
import os
import struct
import tempfile
import zlib
from pathlib import Path
from typing import Dict

import numpy as np
import soundfile as sf
from numba import njit

# 5 byte magic because the original NEON2 container used a 5 byte magic field.
MAGIC = b"LUM8!"
LEGACY_MAGIC = b"NEON2"
VERSION_LEGACY = 1
VERSION = 2
VERSION_LOSSLESS_TURBO = 3

HEADER_V1 = "<5sBIIBH"
HEADER_V2 = "<5sBIIBHBBH"  # v1 fields + mode_id + default_bits + reserved
# v3 lossless turbo: LUM8! + version + audio shape + mode/order/compression + PCM CRC32.
HEADER_LOSSLESS_TURBO = "<5sBIIBHBBBI"

MODE_XLOW = 0
MODE_LOW = 1
MODE_MEDIUM = 2
MODE_BEST = 3
MODE_ULTRA = 4
MODE_LOSSLESS_TURBO = 100

LOSSLESS_COMPRESSION_NONE = 0
LOSSLESS_COMPRESSION_ZSTD = 1
LOSSLESS_COMPRESSION_ZLIB = 2

PRESETS: Dict[str, Dict[str, int]] = {
    "xlow": {"mode_id": MODE_XLOW, "bits": 0, "lookahead": 0},
    "low": {"mode_id": MODE_LOW, "bits": 3, "lookahead": 0},
    "medium": {"mode_id": MODE_MEDIUM, "bits": 4, "lookahead": 0},
    "best": {"mode_id": MODE_BEST, "bits": 4, "lookahead": 1},
    "ultra": {"mode_id": MODE_ULTRA, "bits": 5, "lookahead": 0},
    "turbo": {"mode_id": MODE_LOSSLESS_TURBO, "bits": 16, "lookahead": 0, "lossless": 1},
}

INDEX_TABLE_4BIT = np.array([-1, -1, -1, -1, 2, 4, 6, 8], dtype=np.int16)
STEP_SIZES = np.array([
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17,
    19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
    50, 55, 60, 66, 73, 80, 88, 97, 107, 118,
    130, 143, 157, 173, 190, 209, 230, 253, 279, 307,
    337, 371, 408, 449, 494, 544, 598, 658, 724, 796,
    876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066,
    2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358,
    5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899,
    15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767
], dtype=np.int32)


def to_int16(audio: np.ndarray) -> np.ndarray:
    audio = np.asarray(audio)
    if audio.ndim == 1:
        audio = audio[:, None]

    if np.issubdtype(audio.dtype, np.integer):
        if audio.dtype == np.int16:
            return audio.copy()
        info = np.iinfo(audio.dtype)
        audio = audio.astype(np.float64) / max(abs(info.min), info.max)

    audio = np.nan_to_num(audio.astype(np.float64), nan=0.0, posinf=1.0, neginf=-1.0)
    audio = np.clip(audio, -1.0, 1.0)
    return np.round(audio * 32767.0).astype(np.int16)


def choose_start_step_index(channel_samples: np.ndarray) -> int:
    if len(channel_samples) < 3:
        return 0

    diffs = np.abs(np.diff(channel_samples.astype(np.int32)))
    if len(diffs) == 0:
        return 0

    target = int(max(7, np.percentile(diffs, 75)))
    return int(np.argmin(np.abs(STEP_SIZES - target)))


def choose_xlow_bits(channel_samples: np.ndarray, start_step_idx: int) -> int:
    """Fast block-level VBR selector. Chooses 2, 3 or 4 bits per sample."""
    if len(channel_samples) < 4:
        return 2

    diffs = np.abs(np.diff(channel_samples.astype(np.int32)))
    if len(diffs) == 0:
        return 2

    p75 = float(np.percentile(diffs, 75))
    p95 = float(np.percentile(diffs, 95))
    base_step = float(STEP_SIZES[int(start_step_idx)])

    # 2-bit works for very calm blocks, 4-bit is reserved for transients.
    if p95 <= base_step * 2.2 and p75 <= base_step * 0.95:
        return 2
    if p95 <= base_step * 5.5:
        return 3
    return 4


@njit(cache=True)
def _clamp16(x):
    if x > 32767:
        return 32767
    if x < -32768:
        return -32768
    return x


@njit(cache=True)
def _index_delta(bits, mag):
    if bits == 2:
        if mag == 0:
            return -1
        return 4

    if bits == 3:
        if mag <= 1:
            return -1
        if mag == 2:
            return 2
        return 6

    if bits == 4:
        if mag <= 3:
            return -1
        if mag == 4:
            return 2
        if mag == 5:
            return 4
        if mag == 6:
            return 6
        return 8

    # 5-bit. Gentle at low magnitudes, aggressive at high magnitudes.
    if mag <= 3:
        return -1
    if mag == 4:
        return 0
    if mag == 5:
        return 1
    if mag == 6:
        return 2
    if mag == 7:
        return 3
    if mag == 8:
        return 4
    if mag == 9:
        return 5
    if mag == 10:
        return 6
    if mag == 11:
        return 7
    if mag == 12:
        return 8
    if mag == 13:
        return 10
    if mag == 14:
        return 12
    return 14


@njit(cache=True)
def _apply_code(pred, step_idx, code, bits, step_sizes):
    sign_bit = 1 << (bits - 1)
    mag_mask = sign_bit - 1
    mag = code & mag_mask
    step = step_sizes[step_idx]

    if bits == 4:
        # Exact IMA-style math used by the original LUM8/NEON2 encoder.
        diff_q = step >> 3
        if mag & 4:
            diff_q += step
        if mag & 2:
            diff_q += step >> 1
        if mag & 1:
            diff_q += step >> 2
    else:
        # Mid-rise quantizer scaled to the selected bit depth.
        levels = 1 << (bits - 1)
        diff_q = (step * ((mag << 1) + 1)) // levels
        if diff_q < 1:
            diff_q = 1

    if code & sign_bit:
        pred -= diff_q
    else:
        pred += diff_q

    pred = _clamp16(pred)
    step_idx += _index_delta(bits, mag)

    if step_idx < 0:
        step_idx = 0
    elif step_idx >= len(step_sizes):
        step_idx = len(step_sizes) - 1

    return pred, step_idx


@njit(cache=True)
def _best_code_greedy(pred, step_idx, target, bits, step_sizes):
    limit = 1 << bits
    best = 0
    best_cost = 9223372036854775807

    for code in range(limit):
        p1, _ = _apply_code(pred, step_idx, code, bits, step_sizes)
        e = target - p1
        cost = e * e
        if cost < best_cost:
            best_cost = cost
            best = code

    return best


@njit(cache=True)
def _best_code_lookahead_4bit(pred, step_idx, target, next_target, has_next, step_sizes):
    best = 0
    best_cost = 9223372036854775807

    for code in range(16):
        p1, s1 = _apply_code(pred, step_idx, code, 4, step_sizes)
        e = target - p1
        cost = e * e

        # One-sample lookahead. This is the quality mode from the previous encoder.
        if has_next:
            next_best = 9223372036854775807
            for n2 in range(16):
                p2, _ = _apply_code(p1, s1, n2, 4, step_sizes)
                e2 = next_target - p2
                c2 = e2 * e2
                if c2 < next_best:
                    next_best = c2
            cost += next_best // 4

        if cost < best_cost:
            best_cost = cost
            best = code

    return best


@njit(cache=True)
def _encode_channel(samples, start_pred, start_step_idx, bits, lookahead, step_sizes):
    n = len(samples)
    codes = np.zeros(n, dtype=np.uint8)
    pred = int(start_pred)
    step_idx = int(start_step_idx)

    for i in range(n):
        if lookahead == 1 and bits == 4:
            has_next = i + 1 < n
            next_target = 0
            if has_next:
                next_target = int(samples[i + 1])
            code = _best_code_lookahead_4bit(
                pred, step_idx, int(samples[i]), next_target, has_next, step_sizes
            )
        else:
            code = _best_code_greedy(pred, step_idx, int(samples[i]), bits, step_sizes)

        codes[i] = code
        pred, step_idx = _apply_code(pred, step_idx, code, bits, step_sizes)

    return codes


@njit(cache=True)
def _pack_codes_variable(codes, bits_per_channel):
    frames_minus_1, channels = codes.shape
    total_bits = 0
    for ch in range(channels):
        total_bits += int(bits_per_channel[ch]) * frames_minus_1

    out = np.zeros((total_bits + 7) // 8, dtype=np.uint8)
    bitpos = 0

    for frame in range(frames_minus_1):
        for ch in range(channels):
            bits = int(bits_per_channel[ch])
            code = int(codes[frame, ch])
            for b in range(bits):
                if code & (1 << b):
                    out[bitpos >> 3] |= 1 << (bitpos & 7)
                bitpos += 1

    return out


# ---- LUM8 v3 lossless turbo: delta3 byteplane + zstd/zlib ----

def _pcm_crc32(pcm: np.ndarray) -> int:
    return zlib.crc32(np.ascontiguousarray(pcm).view(np.uint8)) & 0xFFFFFFFF


def _byteplane_pack_u16(values: np.ndarray) -> bytes:
    u = np.ascontiguousarray(values.astype(np.uint16, copy=False))
    b = u.view(np.uint8).reshape(-1, 2)
    return np.ascontiguousarray(np.concatenate([b[:, 0], b[:, 1]])).tobytes()


def _lossless_delta3_payload(pcm: np.ndarray) -> bytes:
    """Third-order fixed predictor residuals, stored as u16 byteplanes."""
    order = 3
    headers = np.ascontiguousarray(pcm[:min(order, pcm.shape[0])]).tobytes()
    if pcm.shape[0] <= order:
        return headers
    x = pcm.astype(np.int32)
    pred = 3 * x[2:-1] - 3 * x[1:-2] + x[:-3]
    residual = ((x[3:] - pred) & 0xFFFF).astype(np.uint16)
    return headers + _byteplane_pack_u16(residual)


def _compress_lossless_payload(data: bytes, level: int = 19) -> tuple[bytes, int]:
    try:
        import zstandard as zstd
        return zstd.ZstdCompressor(level=int(level)).compress(data), LOSSLESS_COMPRESSION_ZSTD
    except Exception:
        return zlib.compress(data, level=min(9, max(1, int(level)))), LOSSLESS_COMPRESSION_ZLIB


def encode_audio_to_lum8_turbo_lossless(
    input_audio: str,
    output_lum8: str,
    block_frames: int = 16384,
    zstd_level: int = 19,
) -> None:
    """Encode exact PCM16 lossless LUM8 v3 turbo."""
    data, sample_rate = sf.read(input_audio, always_2d=True)
    audio = to_int16(data)
    frames, channels = audio.shape

    if channels > 255:
        raise ValueError("LUM8 supports a maximum of 255 channels.")
    if frames > 0xFFFFFFFF:
        raise ValueError("This LUM8 encoder supports up to 4,294,967,295 frames.")
    if block_frames < 0 or block_frames > 65535:
        raise ValueError("block_frames must be between 0 and 65535.")

    raw_payload = _lossless_delta3_payload(audio)
    payload, compression_id = _compress_lossless_payload(raw_payload, zstd_level)
    crc = _pcm_crc32(audio)

    with open(output_lum8, "wb") as f:
        f.write(struct.pack(
            HEADER_LOSSLESS_TURBO,
            MAGIC,
            VERSION_LOSSLESS_TURBO,
            int(sample_rate),
            int(frames),
            int(channels),
            int(block_frames),
            int(MODE_LOSSLESS_TURBO),
            3,  # predictor order
            int(compression_id),
            int(crc),
        ))
        f.write(payload)


def encode_audio_to_lum8(
    input_audio: str,
    output_lum8: str,
    block_frames: int = 2048,
    preset: str = "best",
    legacy_best: bool = False,
    zstd_level: int = 19,
) -> None:
    preset = preset.lower()
    if preset not in PRESETS:
        raise ValueError(f"Unknown preset: {preset}. Choose: {', '.join(PRESETS)}")

    if preset == "turbo":
        encode_audio_to_lum8_turbo_lossless(input_audio, output_lum8, block_frames=block_frames, zstd_level=zstd_level)
        return

    spec = PRESETS[preset]
    mode_id = int(spec["mode_id"])
    default_bits = int(spec["bits"])
    lookahead = int(spec["lookahead"])

    if legacy_best and preset != "best":
        raise ValueError("--legacy-best can only be used with --preset best.")

    data, sample_rate = sf.read(input_audio, always_2d=True)
    audio = to_int16(data)
    frames, channels = audio.shape

    if channels > 255:
        raise ValueError("LUM8 supports a maximum of 255 channels.")
    if block_frames < 2 or block_frames > 65535:
        raise ValueError("block_frames must be between 2 and 65535.")
    if frames > 0xFFFFFFFF:
        raise ValueError("This LUM8 encoder supports up to 4,294,967,295 frames.")

    with open(output_lum8, "wb") as f:
        if legacy_best:
            f.write(struct.pack(
                HEADER_V1,
                MAGIC,
                VERSION_LEGACY,
                int(sample_rate),
                int(frames),
                int(channels),
                int(block_frames),
            ))
        else:
            f.write(struct.pack(
                HEADER_V2,
                MAGIC,
                VERSION,
                int(sample_rate),
                int(frames),
                int(channels),
                int(block_frames),
                int(mode_id),
                int(default_bits),
                0,
            ))

        for start in range(0, frames, block_frames):
            block = audio[start:start + block_frames]
            bframes = block.shape[0]

            start_preds = block[0, :].astype(np.int16)
            start_steps = np.array(
                [choose_start_step_index(block[:, ch]) for ch in range(channels)],
                dtype=np.uint8,
            )

            bits_per_channel = np.zeros(channels, dtype=np.uint8)
            for ch in range(channels):
                if legacy_best:
                    bits_per_channel[ch] = 4
                elif preset == "xlow":
                    bits_per_channel[ch] = choose_xlow_bits(block[:, ch], int(start_steps[ch]))
                else:
                    bits_per_channel[ch] = default_bits

            # v1 block header: first PCM sample + starting step index.
            # v2 block header: first PCM sample + starting step index + bits for this block/channel.
            for ch in range(channels):
                if legacy_best:
                    f.write(struct.pack("<hB", int(start_preds[ch]), int(start_steps[ch])))
                else:
                    f.write(struct.pack(
                        "<hBB",
                        int(start_preds[ch]),
                        int(start_steps[ch]),
                        int(bits_per_channel[ch]),
                    ))

            if bframes <= 1:
                continue

            codes = np.zeros((bframes - 1, channels), dtype=np.uint8)
            for ch in range(channels):
                ch_bits = int(bits_per_channel[ch])
                codes[:, ch] = _encode_channel(
                    block[1:, ch].astype(np.int16),
                    int(start_preds[ch]),
                    int(start_steps[ch]),
                    ch_bits,
                    lookahead,
                    STEP_SIZES,
                )

            if legacy_best:
                # Same nibble order as the original encoder: high nibble first.
                flat = codes.reshape(-1)
                out = np.zeros((len(flat) + 1) // 2, dtype=np.uint8)
                out[: len(flat) // 2] = (flat[0:len(flat) // 2 * 2:2] << 4) | flat[1:len(flat) // 2 * 2:2]
                if len(flat) % 2:
                    out[-1] = flat[-1] << 4
                f.write(out.tobytes())
            else:
                f.write(_pack_codes_variable(codes, bits_per_channel).tobytes())


def compress_lum8z(input_file: str, output_file: str, level: int = 10) -> str:
    """Compress LUM8. Uses zstandard when installed, otherwise zlib fallback."""
    raw = Path(input_file).read_bytes()

    try:
        import zstandard as zstd
        packed = zstd.compress(raw, level=level)
        codec = "zstd"
    except ImportError:
        import zlib
        packed = zlib.compress(raw, level=min(9, max(1, level)))
        codec = "zlib"

    Path(output_file).write_bytes(packed)
    return codec


def main():
    parser = argparse.ArgumentParser(description="LUM8 encoder: WAV/FLAC/AIFF -> .lum8 / .lum8z")
    parser.add_argument("input_audio", help="Input audio file, for example input.wav or input.flac")
    parser.add_argument("output_lum8", help="Output file, for example output.lum8 or output.lum8z")
    parser.add_argument("--block", type=int, default=2048, help="Block size in frames. Default: 2048")
    parser.add_argument(
        "--preset",
        choices=sorted(PRESETS.keys()),
        default="best",
        help="Quality/size preset. Use turbo for fast exact lossless LUM8. Default: best",
    )
    parser.add_argument("--zst", action="store_true", help="Compress output as .lum8z")
    parser.add_argument("--zst-level", type=int, default=10, help="Outer .lum8z zstd/zlib compression level. Default: 10")
    parser.add_argument("--turbo-level", type=int, default=19, help="Internal zstd/zlib level for --preset turbo lossless. Default: 19")
    parser.add_argument(
        "--legacy-best",
        action="store_true",
        help="Write old version-1 4-bit best stream. Only valid with --preset best.",
    )

    args = parser.parse_args()

    output_lower = args.output_lum8.lower()
    output_is_zst = output_lower.endswith(".lum8z") or output_lower.endswith(".zst") or args.zst

    if output_is_zst:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".lum8") as tmp:
            tmp_lum8 = tmp.name

        try:
            encode_audio_to_lum8(
                args.input_audio,
                tmp_lum8,
                block_frames=args.block,
                preset=args.preset,
                legacy_best=args.legacy_best,
                zstd_level=args.turbo_level,
            )
            codec = compress_lum8z(tmp_lum8, args.output_lum8, level=args.zst_level)
        finally:
            if os.path.exists(tmp_lum8):
                os.remove(tmp_lum8)
        print(f"Done: {args.output_lum8} ({args.preset}, compressed with {codec})")
    else:
        encode_audio_to_lum8(
            args.input_audio,
            args.output_lum8,
            block_frames=args.block,
            preset=args.preset,
            legacy_best=args.legacy_best,
            zstd_level=args.turbo_level,
        )
        print(f"Done: {args.output_lum8} ({args.preset})")


if __name__ == "__main__":
    main()
