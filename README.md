# LUM8

### Low-weight-Universal-Music-8

**Alias:** NEON · **Project release:** 5.0

LUM8 is an audio codec that reduces PCM audio size using adaptive differential encoding. It includes a Python encoder and decoder, several lossy presets, and a PCM16 lossless coding mode.

This README describes the **Python implementation**. A native C++ port has a separate command-line interface, build process, and input-format support.

> **PCM16 accuracy notice:** The reviewed Python encoder reads audio as floating point and rescales it by 32767 when converting to PCM16. This can change samples from a 16-bit source before encoding. The `turbo` coding stage preserves the converted PCM16 samples, but this implementation should not be described as bit-perfect against the source file until its input conversion is corrected. Higher-bit-depth sources are also reduced to 16 bits.

## Features

- Adaptive 2–5-bit lossy differential coding.
- Fixed-bit-depth presets and block-level variable bit allocation.
- One-sample lookahead in the `best` preset.
- A `turbo` mode using reversible prediction and compressed PCM16 residuals.
- Mono and stereo support.
- Optional whole-file compression through `.lum8z`.
- WAV output from the decoder.

## Installation

Install Python and the required packages:

```bash
python -m pip install numpy soundfile numba zstandard
```

Download `lum8_encoder.py` and `lum8_decoder.py` from this repository.

The first encode or decode may include Numba compilation time. Subsequent runs can reuse cached compiled functions.

## Quick start

Encode a supported audio file:

```bash
python lum8_encoder.py input.flac output.lum8
```

Encode with additional whole-file compression:

```bash
python lum8_encoder.py input.flac output.lum8z
```

Decode either extension to WAV:

```bash
python lum8_decoder.py output.lum8 restored.wav
python lum8_decoder.py output.lum8z restored.wav
```

The encoder reads audio through SoundFile/libsndfile, including supported WAV, FLAC, and AIFF files. Available input formats depend on the installed library.

## Presets

| Preset | Coding | Notes |
|---|---|---|
| `xlow` | Adaptive 2–4 bits per coded sample | block-level bit allocation; use with caution. |
| `low` | Fixed 3 bits | Smaller lossy output, with greater potential distortion. |
| `medium` | Fixed 4 bits | Greedy code search. |
| `best` | Fixed 4 bits | Default; adds one-sample lookahead at a higher encoding cost. |
| `ultra` | Fixed 5 bits | Larger lossy output with finer quantization. |
| `turbo` | PCM16 lossless coding stage | Preserves the converted PCM16 data; see the input-conversion notice above. |

Select a preset explicitly:

```bash
python lum8_encoder.py input.wav output.lum8 --preset medium
python lum8_encoder.py input.wav output.lum8 --preset best
python lum8_encoder.py input.wav output.lum8 --preset ultra
python lum8_encoder.py input.wav output.lum8 --preset turbo
```

`medium` and `best` produce the same uncompressed LUM8 size when the source shape and block size match. Their code choices and encoding time differ, and their `.lum8z` sizes can differ.

Preset names do not guarantee a particular listening quality or speed on every source. Compare decoded audio against the original before choosing a setting.

## Compression and block settings

Use `.lum8z` to apply outer compression automatically:

```bash
python lum8_encoder.py input.wav output.lum8z --preset best
```

Control the outer compression level:

```bash
python lum8_encoder.py input.wav output.lum8z --preset best --zst-level 10
```

Change the lossy block size in frames:

```bash
python lum8_encoder.py input.wav output.lum8 --preset best --block 2048
```

Smaller blocks increase header overhead and reset predictor state more frequently. Their effect on quality depends on the source.

For `turbo`, control the internal compression level:

```bash
python lum8_encoder.py input.wav output.lum8 --preset turbo --turbo-level 19
```

Turbo is already internally compressed. Wrapping it in `.lum8z` can add encoding time and may make the file slightly larger.

## How lossy coding works

1. Read the input and convert it to PCM16.
2. Split the audio into blocks.
3. Store each channel's first sample and initial step index.
4. Encode subsequent samples as small adaptive differential codes.
5. Update the reconstructed predictor and step index after each code.
6. Pack the codes into the output bitstream.

In 4-bit mode, a code contains a sign bit and three magnitude bits. Two codes fit into one byte. Each code represents one sample in one channel, rather than an entire stereo frame.

The decoder reconstructs each sample from its previous reconstructed value and the selected step size. The 4-bit reconstruction uses IMA-style arithmetic.

The encoder searches possible codes for a low squared reconstruction error. `best` also evaluates a weighted error for the next sample. This search improves its coding decisions but requires substantially more work than greedy encoding.

Lossy reconstruction can change waveform peaks and transients. Low average error does not guarantee accurate individual peaks or inaudible distortion.

## How turbo coding works

Turbo does not use FLAC internally. It uses:

- A fixed third-order predictor.
- Reversible residual arithmetic modulo 65536.
- Byteplane packing.
- Zstandard compression, with a zlib fallback.
- CRC32 verification of the converted PCM16 audio.

The decoder reverses these steps and verifies the PCM checksum. CRC32 detects corruption of the stored PCM representation; it does not prove that input conversion preserved the original source samples.

Lossless here refers to PCM sample values, not restoration of the original WAV/FLAC container, tags, or other metadata.

## File layout

The project release number and the container version are separate. The current encoder writes version 2 for normal lossy output and version 3 for turbo. The decoder also supports the legacy version 1 layout.

| Container | Contents |
|---|---|
| Lossy v2 | Main header followed by block headers and packed codes. |
| Turbo v3 | Main header, PCM CRC32, and a single internally compressed payload. |
| `.lum8z` | An outer compressed wrapper around a LUM8 file. |

Lossy block headers store the first sample, initial step index, and code bit depth for each channel. Main headers describe the sample rate, frame count, channel count, and coding settings.

In the current turbo implementation, `block_frames` is stored in the header but does not divide the lossless payload into independently compressed blocks.

With Zstandard installed, `.lum8z` uses Zstandard. The Python encoder falls back to zlib if it is unavailable. The supplied decoder handles both wrappers directly; manual decompression is unnecessary.

## Example images

These are illustrations from one source, not a general quality or compression benchmark. File sizes and spectrograms alone do not establish audible transparency or superiority over another codec.

### WAV — approximately 44 kHz, 34,764 kB

![Original WAV spectrogram](https://github.com/user-attachments/assets/c46a5733-d892-43ca-b13d-de2f4414931d)

### LUM8 — 8,713 kB; LUM8Z — 7,495 kB

![LUM8 spectrogram](https://github.com/user-attachments/assets/290aff89-492e-4c6f-8857-89638626beaa)

### MP3 — 256 kbit/s, 8,819 kB

![MP3 spectrogram](https://github.com/user-attachments/assets/a7e91c67-c691-4d24-89c4-8fa00b44aa12)

## Limitations and development priorities

- Correct PCM16 input conversion before claiming source-bit-perfect encoding.
- No preservation of 24-bit or floating-point source precision.
- No built-in preservation of source tags or cover art.
- Whole-file buffering and decompression can require substantial memory.
- Turbo has no independently compressed blocks for efficient random access.
- Lossy quality depends on the preset, block size, and source.
- No guaranteed size advantage over FLAC or quality advantage over established lossy codecs.
- Native playback requires an appropriate decoder integration in the target player.
- Further work is needed on malformed-file validation, encoding speed, and progress reporting.

For meaningful comparisons, measure actual file size and encoding/decoding time, compare PCM reconstruction errors, and perform level-matched blind listening tests. Separate first-run compilation time from steady-state codec performance.

## Name

**LUM** stands for **Low-weight Universal Music**. The **8** represents the infinity symbol.

## License

MIT License. See [LICENSE](LICENSE) for the full terms and copyright notice.
