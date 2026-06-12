#!/usr/bin/env python3
"""LUM8 encoder: WAV/FLAC/AIFF/OGG -> .lum8 / .lum8z

LUM8 = Low-weight-Universal-Music-8
Alias: NEON / NEON2
"""

import argparse
import os
import struct
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
from numba import njit

# 5 byte magic because the original NEON2 container used a 5 byte magic field.
MAGIC = b"LUM8!"
LEGACY_MAGIC = b"NEON2"
VERSION = 1

INDEX_TABLE = np.array([-1, -1, -1, -1, 2, 4, 6, 8], dtype=np.int16)
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


@njit
def _apply_nibble(pred, step_idx, nibble, step_sizes, index_table):
    step = step_sizes[step_idx]
    diff_q = step >> 3

    if nibble & 4:
        diff_q += step
    if nibble & 2:
        diff_q += step >> 1
    if nibble & 1:
        diff_q += step >> 2

    if nibble & 8:
        pred -= diff_q
    else:
        pred += diff_q

    if pred > 32767:
        pred = 32767
    elif pred < -32768:
        pred = -32768

    step_idx += index_table[nibble & 7]

    if step_idx < 0:
        step_idx = 0
    elif step_idx >= len(step_sizes):
        step_idx = len(step_sizes) - 1

    return pred, step_idx


@njit
def _best_nibble(pred, step_idx, target, next_target, has_next, step_sizes, index_table):
    best = 0
    best_cost = 9223372036854775807

    for nibble in range(16):
        p1, s1 = _apply_nibble(pred, step_idx, nibble, step_sizes, index_table)
        e = target - p1
        cost = e * e

        # One-sample lookahead. Sometimes a slightly worse current value gives a better next value.
        if has_next:
            next_best = 9223372036854775807
            for n2 in range(16):
                p2, _ = _apply_nibble(p1, s1, n2, step_sizes, index_table)
                e2 = next_target - p2
                c2 = e2 * e2
                if c2 < next_best:
                    next_best = c2
            cost += next_best // 4

        if cost < best_cost:
            best_cost = cost
            best = nibble

    return best


@njit
def _encode_channel(samples, start_pred, start_step_idx, step_sizes, index_table):
    n = len(samples)
    nibbles = np.zeros(n, dtype=np.uint8)
    pred = int(start_pred)
    step_idx = int(start_step_idx)

    for i in range(n):
        has_next = i + 1 < n
        next_target = 0
        if has_next:
            next_target = int(samples[i + 1])

        nibble = _best_nibble(
            pred,
            step_idx,
            int(samples[i]),
            next_target,
            has_next,
            step_sizes,
            index_table,
        )
        nibbles[i] = nibble
        pred, step_idx = _apply_nibble(pred, step_idx, nibble, step_sizes, index_table)

    return nibbles


@njit
def _pack_nibbles(nibbles_flat):
    out = np.zeros((len(nibbles_flat) + 1) // 2, dtype=np.uint8)
    j = 0

    for i in range(0, len(nibbles_flat), 2):
        hi = nibbles_flat[i] & 15
        lo = 0

        if i + 1 < len(nibbles_flat):
            lo = nibbles_flat[i + 1] & 15

        out[j] = (hi << 4) | lo
        j += 1

    return out


def encode_audio_to_lum8(input_audio: str, output_lum8: str, block_frames: int = 2048) -> None:
    data, sample_rate = sf.read(input_audio, always_2d=True)
    audio = to_int16(data)
    frames, channels = audio.shape

    if channels > 255:
        raise ValueError("LUM8 supports a maximum of 255 channels.")
    if block_frames < 2 or block_frames > 65535:
        raise ValueError("block_frames must be between 2 and 65535.")

    with open(output_lum8, "wb") as f:
        # Header: magic, version, sample rate, total frames, channels, block size.
        f.write(struct.pack("<5sBIIBH", MAGIC, VERSION, int(sample_rate), frames, channels, block_frames))

        for start in range(0, frames, block_frames):
            block = audio[start:start + block_frames]
            bframes = block.shape[0]

            start_preds = block[0, :].astype(np.int16)
            start_steps = np.array(
                [choose_start_step_index(block[:, ch]) for ch in range(channels)],
                dtype=np.uint8,
            )

            # Per-channel block header: first PCM sample + starting step index.
            for ch in range(channels):
                f.write(struct.pack("<hB", int(start_preds[ch]), int(start_steps[ch])))

            if bframes <= 1:
                continue

            nibs = np.zeros((bframes - 1, channels), dtype=np.uint8)

            for ch in range(channels):
                nibs[:, ch] = _encode_channel(
                    block[1:, ch].astype(np.int16),
                    int(start_preds[ch]),
                    int(start_steps[ch]),
                    STEP_SIZES,
                    INDEX_TABLE,
                )

            f.write(_pack_nibbles(nibs.reshape(-1)).tobytes())


def compress_zst(input_file: str, output_file: str, level: int = 10) -> None:
    try:
        import zstandard as zstd
    except ImportError as exc:
        raise SystemExit("Install first: pip install zstandard") from exc

    raw = Path(input_file).read_bytes()
    packed = zstd.compress(raw, level=level)
    Path(output_file).write_bytes(packed)


def main():
    parser = argparse.ArgumentParser(description="LUM8 encoder: WAV/FLAC/AIFF/OGG -> .lum8 / .lum8z")
    parser.add_argument("input_audio", help="Input audio file, for example input.wav or input.flac")
    parser.add_argument("output_lum8", help="Output file, for example output.lum8 or output.lum8z")
    parser.add_argument("--block", type=int, default=2048, help="Block size in frames. Default: 2048")
    parser.add_argument("--zst", action="store_true", help="Compress output with zstandard")
    parser.add_argument("--zst-level", type=int, default=10, help="zstd level. Default: 10")

    args = parser.parse_args()

    output_lower = args.output_lum8.lower()
    output_is_zst = output_lower.endswith(".lum8z") or output_lower.endswith(".zst") or args.zst

    if output_is_zst:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".lum8") as tmp:
            tmp_lum8 = tmp.name

        try:
            encode_audio_to_lum8(args.input_audio, tmp_lum8, block_frames=args.block)
            compress_zst(tmp_lum8, args.output_lum8, level=args.zst_level)
        finally:
            if os.path.exists(tmp_lum8):
                os.remove(tmp_lum8)
    else:
        encode_audio_to_lum8(args.input_audio, args.output_lum8, block_frames=args.block)

    print(f"Done: {args.output_lum8}")


if __name__ == "__main__":
    main()
