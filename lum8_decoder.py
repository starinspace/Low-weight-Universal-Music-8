#!/usr/bin/env python3
"""LUM8 decoder: .lum8 / .lum8z -> WAV

Also reads old NEON2 files for backward compatibility.
"""

import argparse
import struct
from pathlib import Path

import numpy as np
import soundfile as sf
from numba import njit

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
def _decode_samples_from_nibbles(nibbles, start_preds, start_steps, frames, channels, step_sizes, index_table):
    out = np.zeros((frames, channels), dtype=np.int16)
    preds = np.zeros(channels, dtype=np.int32)
    steps = np.zeros(channels, dtype=np.int32)

    for ch in range(channels):
        preds[ch] = int(start_preds[ch])
        steps[ch] = int(start_steps[ch])
        out[0, ch] = start_preds[ch]

    k = 0

    for frame in range(1, frames):
        for ch in range(channels):
            nibble = int(nibbles[k])
            k += 1

            pred, step_idx = _apply_nibble(preds[ch], steps[ch], nibble, step_sizes, index_table)
            preds[ch] = pred
            steps[ch] = step_idx
            out[frame, ch] = pred

    return out


def _unpack_nibbles(payload: bytes, wanted: int) -> np.ndarray:
    b = np.frombuffer(payload, dtype=np.uint8)
    out = np.zeros(len(b) * 2, dtype=np.uint8)
    out[0::2] = (b >> 4) & 15
    out[1::2] = b & 15
    return out[:wanted]


def read_maybe_lum8z(input_file: str) -> bytes:
    raw = Path(input_file).read_bytes()

    if raw.startswith(MAGIC) or raw.startswith(LEGACY_MAGIC):
        return raw

    name = input_file.lower()
    if name.endswith(".lum8z") or name.endswith(".zst"):
        try:
            import zstandard as zstd
        except ImportError as exc:
            raise SystemExit("Install first: pip install zstandard") from exc

        return zstd.decompress(raw)

    return raw


def decode_lum8_to_wav(input_lum8: str, output_wav: str) -> None:
    raw = read_maybe_lum8z(input_lum8)
    header_size = struct.calcsize("<5sBIIBH")

    if len(raw) < header_size:
        raise ValueError("The file is too small to be a LUM8 file.")

    magic, version, sample_rate, frames, channels, block_frames = struct.unpack("<5sBIIBH", raw[:header_size])

    if magic not in (MAGIC, LEGACY_MAGIC) or version != VERSION:
        raise ValueError("This decoder only reads LUM8 files, or legacy NEON2 files.")

    offset = header_size
    out = np.zeros((frames, channels), dtype=np.int16)
    pos = 0

    while pos < frames:
        bframes = min(block_frames, frames - pos)
        start_preds = np.zeros(channels, dtype=np.int16)
        start_steps = np.zeros(channels, dtype=np.uint8)

        for ch in range(channels):
            if offset + 3 > len(raw):
                raise ValueError("Broken LUM8 file: missing block header.")
            start_preds[ch], start_steps[ch] = struct.unpack("<hB", raw[offset:offset + 3])
            offset += 3

        wanted_nibbles = max(0, (bframes - 1) * channels)
        wanted_bytes = (wanted_nibbles + 1) // 2

        if offset + wanted_bytes > len(raw):
            raise ValueError("Broken LUM8 file: missing audio data.")

        payload = raw[offset:offset + wanted_bytes]
        offset += wanted_bytes

        nibbles = _unpack_nibbles(payload, wanted_nibbles)

        out[pos:pos + bframes] = _decode_samples_from_nibbles(
            nibbles,
            start_preds,
            start_steps,
            bframes,
            channels,
            STEP_SIZES,
            INDEX_TABLE,
        )

        pos += bframes

    if channels == 1:
        out = out[:, 0]

    sf.write(output_wav, out, sample_rate, subtype="PCM_16")


def main():
    parser = argparse.ArgumentParser(description="LUM8 decoder: .lum8 / .lum8z -> WAV")
    parser.add_argument("input_lum8", help="Input file, for example input.lum8 or input.lum8z")
    parser.add_argument("output_wav", help="Output WAV file, for example restored.wav")
    args = parser.parse_args()

    decode_lum8_to_wav(args.input_lum8, args.output_wav)
    print(f"Done: {args.output_wav}")


if __name__ == "__main__":
    main()
