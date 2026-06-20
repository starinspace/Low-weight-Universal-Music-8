#!/usr/bin/env python3
"""LUM8 decoder: .lum8 / .lum8z -> WAV

Reads LUM8 v3 lossless turbo, LUM8 v2 lossy presets, and old LUM8/NEON2 v1 files.
"""

import argparse
import struct
import zlib
from pathlib import Path

import numpy as np
import soundfile as sf
from numba import njit

MAGIC = b"LUM8!"
LEGACY_MAGIC = b"NEON2"
VERSION_LEGACY = 1
VERSION = 2
VERSION_LOSSLESS_TURBO = 3

HEADER_V1 = "<5sBIIBH"
HEADER_V2 = "<5sBIIBHBBH"
HEADER_LOSSLESS_TURBO = "<5sBIIBHBBBI"
MODE_LOSSLESS_TURBO = 100
LOSSLESS_COMPRESSION_NONE = 0
LOSSLESS_COMPRESSION_ZSTD = 1
LOSSLESS_COMPRESSION_ZLIB = 2

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

GATE_PROFILES = {
    "off": {"attenuation": 1.00, "floor_scale": 0.00, "percentile": 66.0, "cutoff_hz": 3800.0, "n_fft": 1024, "hop": 512},
    "mild": {"attenuation": 0.96, "floor_scale": 0.16, "percentile": 66.0, "cutoff_hz": 3800.0, "n_fft": 1024, "hop": 512},
    "medium": {"attenuation": 0.78, "floor_scale": 0.20, "percentile": 64.0, "cutoff_hz": 3600.0, "n_fft": 1024, "hop": 512},
    "strong": {"attenuation": 0.64, "floor_scale": 0.23, "percentile": 62.0, "cutoff_hz": 3400.0, "n_fft": 1024, "hop": 512},
    "hard": {"attenuation": 0.48, "floor_scale": 0.27, "percentile": 60.0, "cutoff_hz": 3200.0, "n_fft": 1024, "hop": 512},
    "xhard": {"attenuation": 0.33, "floor_scale": 0.32, "percentile": 58.0, "cutoff_hz": 3000.0, "n_fft": 1024, "hop": 512},
    "superhard": {"attenuation": 0.18, "floor_scale": 0.38, "percentile": 55.0, "cutoff_hz": 2800.0, "n_fft": 1024, "hop": 512},
}


def _resolve_gate_profile(gate_profile: str, mode_id: int, default_bits: int, tiny_filter=None) -> str:
    """Return the actual decoder postfilter profile. Default is intentionally off."""
    gp = (gate_profile or "off").lower()
    if gp in ("auto", "safe", "off"):
        return "off"
    if gp == "denoise":
        # Manual problem-file mode: stronger for small presets, gentler for large presets.
        if default_bits <= 3:
            return "superhard"
        if default_bits == 4:
            return "hard"
        return "medium"
    if gp in GATE_PROFILES:
        return gp
    raise ValueError(f"Unknown gate profile: {gate_profile}. Choose off, denoise, mild, medium, strong, hard, xhard or superhard.")


def _spectral_gate_int16(audio_int16: np.ndarray, sample_rate: int, profile_name: str) -> np.ndarray:
    if profile_name == "off":
        return audio_int16
    p = GATE_PROFILES[profile_name]
    audio = audio_int16.astype(np.float32) / 32768.0
    one_dim = False
    if audio.ndim == 1:
        audio = audio[:, None]
        one_dim = True

    n_fft = int(p["n_fft"])
    hop = int(p["hop"])
    if audio.shape[0] < n_fft:
        return audio_int16

    win = np.hanning(n_fft).astype(np.float32)
    out = np.zeros_like(audio, dtype=np.float32)
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / float(sample_rate))
    high = freqs >= float(p["cutoff_hz"])
    attenuation = float(p["attenuation"])
    floor_scale = float(p["floor_scale"])
    percentile = float(p["percentile"])

    for ch in range(audio.shape[1]):
        x = audio[:, ch]
        ch_out = np.zeros(audio.shape[0], dtype=np.float32)
        ch_norm = np.zeros(audio.shape[0], dtype=np.float32)
        for start in range(0, audio.shape[0] - n_fft + 1, hop):
            frame = x[start:start + n_fft] * win
            spec = np.fft.rfft(frame)
            mag = np.abs(spec)
            if np.any(high):
                hf = mag[high]
                floor = float(np.percentile(hf, percentile)) * floor_scale
                weak = high & (mag <= floor)
                if np.any(weak):
                    spec[weak] *= attenuation
            rec = np.fft.irfft(spec, n_fft).astype(np.float32) * win
            ch_out[start:start + n_fft] += rec
            ch_norm[start:start + n_fft] += win * win
        mask = ch_norm > 1e-8
        restored = x.copy()
        restored[mask] = ch_out[mask] / ch_norm[mask]
        out[:, ch] = restored

    y = np.clip(np.round(out * 32767.0), -32768, 32767).astype(np.int16)
    if one_dim:
        return y[:, 0]
    return y



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
        diff_q = step >> 3
        if mag & 4:
            diff_q += step
        if mag & 2:
            diff_q += step >> 1
        if mag & 1:
            diff_q += step >> 2
    else:
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
def _decode_samples_from_codes(codes, start_preds, start_steps, bits_per_channel, frames, channels, step_sizes):
    out = np.zeros((frames, channels), dtype=np.int16)
    preds = np.zeros(channels, dtype=np.int32)
    steps = np.zeros(channels, dtype=np.int32)

    for ch in range(channels):
        preds[ch] = int(start_preds[ch])
        steps[ch] = int(start_steps[ch])
        out[0, ch] = start_preds[ch]

    for frame in range(1, frames):
        for ch in range(channels):
            code = int(codes[frame - 1, ch])
            bits = int(bits_per_channel[ch])
            pred, step_idx = _apply_code(preds[ch], steps[ch], code, bits, step_sizes)
            preds[ch] = pred
            steps[ch] = step_idx
            out[frame, ch] = pred

    return out


@njit(cache=True)
def _unpack_codes_variable(payload, frames_minus_1, channels, bits_per_channel):
    codes = np.zeros((frames_minus_1, channels), dtype=np.uint8)
    bitpos = 0

    for frame in range(frames_minus_1):
        for ch in range(channels):
            bits = int(bits_per_channel[ch])
            code = 0
            for b in range(bits):
                byte = int(payload[bitpos >> 3])
                if byte & (1 << (bitpos & 7)):
                    code |= 1 << b
                bitpos += 1
            codes[frame, ch] = code

    return codes


def _unpack_legacy_nibbles(payload: bytes, wanted: int) -> np.ndarray:
    b = np.frombuffer(payload, dtype=np.uint8)
    out = np.zeros(len(b) * 2, dtype=np.uint8)
    out[0::2] = (b >> 4) & 15
    out[1::2] = b & 15
    return out[:wanted]


# ---- LUM8 v3 lossless turbo: delta3 byteplane + zstd/zlib ----

def _pcm_crc32(pcm: np.ndarray) -> int:
    return zlib.crc32(np.ascontiguousarray(pcm).view(np.uint8)) & 0xFFFFFFFF


def _decompress_lossless_payload(data: bytes, compression_id: int) -> bytes:
    if compression_id == LOSSLESS_COMPRESSION_NONE:
        return data
    if compression_id == LOSSLESS_COMPRESSION_ZSTD:
        import zstandard as zstd
        return zstd.ZstdDecompressor().decompress(data)
    if compression_id == LOSSLESS_COMPRESSION_ZLIB:
        return zlib.decompress(data)
    raise ValueError(f"Unknown LUM8 lossless compression id: {compression_id}")


def _byteplane_unpack_u16(data: bytes, count: int) -> np.ndarray:
    raw = np.frombuffer(data, dtype=np.uint8)
    if raw.size != count * 2:
        raise ValueError("Invalid LUM8 lossless byteplane length")
    out = np.empty((count, 2), dtype=np.uint8)
    out[:, 0] = raw[:count]
    out[:, 1] = raw[count:]
    return out.reshape(-1).view(np.uint16).copy()


def _decode_lossless_delta3_payload(raw: bytes, frames: int, channels: int) -> np.ndarray:
    order = 3
    out = np.empty((frames, channels), dtype=np.int16)
    if frames == 0:
        return out
    header_frames = min(order, frames)
    header_bytes = header_frames * channels * 2
    if len(raw) < header_bytes:
        raise ValueError("Broken LUM8 lossless file: missing warm-up samples.")
    init = np.frombuffer(raw[:header_bytes], dtype=np.int16).reshape(header_frames, channels).astype(np.int32)
    out[:header_frames] = init.astype(np.int16)
    if frames <= order:
        return out
    count = (frames - order) * channels
    residual = _byteplane_unpack_u16(raw[header_bytes:], count).reshape(frames - order, channels)
    hist = [init[i].astype(np.int32) for i in range(header_frames)]
    for i in range(order, frames):
        pred = 3 * hist[-1] - 3 * hist[-2] + hist[-3]
        cur_u = (pred.astype(np.uint16).astype(np.uint32) + residual[i - order].astype(np.uint32)) & 0xFFFF
        cur = cur_u.astype(np.uint16).view(np.int16).astype(np.int32)
        out[i] = cur.astype(np.int16)
        hist.append(cur)
    return out


def _decode_lum8_turbo_lossless(raw: bytes, output_wav: str) -> None:
    header_size = struct.calcsize(HEADER_LOSSLESS_TURBO)
    if len(raw) < header_size:
        raise ValueError("Broken LUM8 lossless file: missing header.")
    magic, version, sample_rate, frames, channels, block_frames, mode_id, order, compression_id, crc = struct.unpack(
        HEADER_LOSSLESS_TURBO,
        raw[:header_size],
    )
    if magic != MAGIC or version != VERSION_LOSSLESS_TURBO or mode_id != MODE_LOSSLESS_TURBO:
        raise ValueError("Broken LUM8 lossless file: invalid header.")
    if order != 3:
        raise ValueError(f"Unsupported LUM8 lossless predictor order: {order}")
    payload = raw[header_size:]
    decoded_payload = _decompress_lossless_payload(payload, int(compression_id))
    out = _decode_lossless_delta3_payload(decoded_payload, int(frames), int(channels))
    if _pcm_crc32(out) != int(crc):
        raise ValueError("LUM8 lossless CRC mismatch.")
    if channels == 1:
        out = out[:, 0]
    sf.write(output_wav, out, int(sample_rate), subtype="PCM_16")


def read_maybe_lum8z(input_file: str) -> bytes:
    raw = Path(input_file).read_bytes()

    if raw.startswith(MAGIC) or raw.startswith(LEGACY_MAGIC):
        return raw

    name = input_file.lower()
    if name.endswith(".lum8z") or name.endswith(".zst"):
        # Prefer zstandard, but support zlib fallback written by the encoder if zstandard is missing.
        try:
            import zstandard as zstd
            try:
                return zstd.decompress(raw)
            except Exception:
                pass
        except ImportError:
            pass

        import zlib
        try:
            return zlib.decompress(raw)
        except Exception as exc:
            raise ValueError("Could not decompress .lum8z. Install zstandard if it was zstd-compressed.") from exc

    return raw


def decode_lum8_to_wav(input_lum8: str, output_wav: str, tiny_filter=None, gate_profile: str = "off") -> None:
    raw = read_maybe_lum8z(input_lum8)
    header_v1_size = struct.calcsize(HEADER_V1)

    if len(raw) < header_v1_size:
        raise ValueError("The file is too small to be a LUM8 file.")

    magic, version, sample_rate, frames, channels, block_frames = struct.unpack(
        HEADER_V1, raw[:header_v1_size]
    )

    if magic not in (MAGIC, LEGACY_MAGIC):
        raise ValueError("This decoder only reads LUM8 files, or legacy NEON2 files.")

    if version == VERSION_LOSSLESS_TURBO:
        _decode_lum8_turbo_lossless(raw, output_wav)
        return

    if version == VERSION_LEGACY:
        offset = header_v1_size
        mode_id = 3
        default_bits = 4
        is_legacy = True
    elif version == VERSION:
        header_v2_size = struct.calcsize(HEADER_V2)
        if len(raw) < header_v2_size:
            raise ValueError("Broken LUM8 v2 file: missing extended header.")
        magic, version, sample_rate, frames, channels, block_frames, mode_id, default_bits, _reserved = struct.unpack(
            HEADER_V2, raw[:header_v2_size]
        )
        offset = header_v2_size
        is_legacy = False
    else:
        raise ValueError(f"Unsupported LUM8 version: {version}")

    if channels < 1:
        raise ValueError("Broken LUM8 file: channel count is zero.")

    out = np.zeros((frames, channels), dtype=np.int16)
    pos = 0

    while pos < frames:
        bframes = min(block_frames, frames - pos)
        start_preds = np.zeros(channels, dtype=np.int16)
        start_steps = np.zeros(channels, dtype=np.uint8)
        bits_per_channel = np.zeros(channels, dtype=np.uint8)

        for ch in range(channels):
            if is_legacy:
                if offset + 3 > len(raw):
                    raise ValueError("Broken LUM8 file: missing block header.")
                start_preds[ch], start_steps[ch] = struct.unpack("<hB", raw[offset:offset + 3])
                bits_per_channel[ch] = 4
                offset += 3
            else:
                if offset + 4 > len(raw):
                    raise ValueError("Broken LUM8 v2 file: missing block header.")
                start_preds[ch], start_steps[ch], bits_per_channel[ch] = struct.unpack(
                    "<hBB", raw[offset:offset + 4]
                )
                if bits_per_channel[ch] < 2 or bits_per_channel[ch] > 6:
                    raise ValueError("Broken LUM8 v2 file: invalid bit depth in block.")
                offset += 4

        frames_minus_1 = max(0, bframes - 1)
        if is_legacy:
            wanted_codes = frames_minus_1 * channels
            wanted_bytes = (wanted_codes + 1) // 2
            if offset + wanted_bytes > len(raw):
                raise ValueError("Broken LUM8 file: missing audio data.")
            payload = raw[offset:offset + wanted_bytes]
            offset += wanted_bytes
            flat = _unpack_legacy_nibbles(payload, wanted_codes)
            codes = flat.reshape((frames_minus_1, channels))
        else:
            total_bits = int(frames_minus_1 * int(np.sum(bits_per_channel.astype(np.int32))))
            wanted_bytes = (total_bits + 7) // 8
            if offset + wanted_bytes > len(raw):
                raise ValueError("Broken LUM8 v2 file: missing audio data.")
            payload = np.frombuffer(raw[offset:offset + wanted_bytes], dtype=np.uint8)
            offset += wanted_bytes
            codes = _unpack_codes_variable(payload, frames_minus_1, channels, bits_per_channel)

        out[pos:pos + bframes] = _decode_samples_from_codes(
            codes,
            start_preds,
            start_steps,
            bits_per_channel,
            bframes,
            channels,
            STEP_SIZES,
        )

        pos += bframes

    actual_gate = _resolve_gate_profile(gate_profile, int(mode_id), int(default_bits), tiny_filter=tiny_filter)
    out = _spectral_gate_int16(out, int(sample_rate), actual_gate)

    if channels == 1:
        out = out[:, 0]

    sf.write(output_wav, out, sample_rate, subtype="PCM_16")


def main():
    parser = argparse.ArgumentParser(description="LUM8 decoder: .lum8 / .lum8z -> WAV")
    parser.add_argument("input_lum8", help="Input file, for example input.lum8 or input.lum8z")
    parser.add_argument("output_wav", help="Output WAV file, for example restored.wav")
    parser.add_argument(
        "--gate-profile",
        default="off",
        choices=["off", "denoise", "mild", "medium", "strong", "hard", "xhard", "superhard"],
        help="Optional decoder postfilter. Default: off. Use denoise only for problem files.",
    )
    args = parser.parse_args()

    decode_lum8_to_wav(args.input_lum8, args.output_wav, gate_profile=args.gate_profile)
    print(f"Done: {args.output_wav}")


if __name__ == "__main__":
    main()
