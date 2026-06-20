# LUM8

### Low-weight-Universal-Music-8

Alias: NEON

TODO:
There is a few bugs I need to fix

This is **LUM8 version 5.0**.

**LUM8** is an experimental audio format designed to reduce PCM audio size using adaptive differential encoding.

LUM8 supports both:

```text
lossless = exact PCM16 restore, bit-perfect (15% bigger than flac, 40% smaller than wav)
lossy    = smaller files
```

The normal LUM8 modes are lossy and use adaptive 2–5 bit differential encoding.
The **turbo** preset is lossless and restores the original PCM16 audio exactly.

## Installation

```bash
pip install numpy soundfile numba zstandard
```

## Usage

Encode WAV/FLAC/AIFF to normal LUM8:

```bash
python lum8_encoder.py input.flac output.lum8
```

Encode to compressed LUM8Z:

```bash
python lum8_encoder.py input.flac output.lum8z
```

Encode lossless LUM8:

```bash
python lum8_encoder.py input.flac output.lum8 --preset turbo
```

Encode lossless compressed LUM8Z (can sometimes make it smaller):

```bash
python lum8_encoder.py input.flac output.lum8z --preset turbo
```

Decode LUM8 or LUM8Z to WAV:

```bash
python lum8_decoder.py input.lum8 output.wav
```

## Examples

WAV 44 kHz (34 764 kB)

<img width="4096" height="2048" alt="WAV" src="https://github.com/user-attachments/assets/c46a5733-d892-43ca-b13d-de2f4414931d" />

LUM8 (8 713 kB) - LUM8Z (7 495 kB)

<img width="4096" height="2048" alt="LUM8" src="https://github.com/user-attachments/assets/290aff89-492e-4c6f-8857-89638626beaa" />

MP3 256k (8 819 kB)

<img width="4096" height="2048" alt="MP3 256k" src="https://github.com/user-attachments/assets/a7e91c67-c691-4d24-89c4-8fa00b44aa12" />

## Presets

```text
xlow    = experimental 2–4 bit block VBR (Not recommended)
low     = fixed 3-bit (good for speach, classical and ambience music)
medium  = fixed 4-bit
best    = fixed 4-bit with one-sample lookahead (Recommended)
ultra   = fixed 5-bit
turbo   = exact lossless PCM16 mode
```

## Short version

LUM8 does not always store every audio sample directly.

In lossy modes, it stores how the sound changes from the previous sample.
Each sample difference is stored using a small code instead of a full 16-bit PCM sample.

In the normal 4-bit mode, each code is a **nibble**:

```text
1 bit   = direction, up or down
3 bits  = change amount
```

Two 4-bit samples fit inside one byte:

```text
sample A = 4 bits
sample B = 4 bits
= 1 byte
```

## How lossy LUM8 works

1. The input audio is read as WAV/FLAC/AIFF.
2. The audio is converted to 16-bit PCM.
3. The audio is split into blocks.
4. Each block stores its first sample and starting step level.
5. Instead of storing full samples, the encoder stores changes from the previous value.
6. Each difference is encoded using 2, 3, 4, or 5 bits depending on the preset.
7. The step size adapts automatically to the movement of the audio.
8. The packed bitstream is written to the LUM8 file.

## How lossless LUM8 works

The **turbo** preset uses exact lossless PCM16 encoding.

It does not use FLAC internally.
Instead, it uses a third-order delta predictor, byteplane packing, compression, and CRC checking.

This means the decoded WAV should match the original PCM16 audio exactly.

## Adaptive step size

Quiet or detailed parts use smaller steps.

Loud or fast-changing parts use larger steps.

This allows LUM8 to follow both soft and strong audio without storing every sample as full 16-bit PCM in lossy modes.

## Smart encoding

The encoder tests possible code values and chooses the one with the lowest error.

The **best** preset also uses a small one-sample lookahead, so it can sometimes choose a slightly less perfect value now if it makes the next sample better.

## File structure

A normal LUM8 file contains:

```text
main header
block 1 header
block 1 packed audio data
block 2 header
block 2 packed audio data
...
```

The main header stores:

```text
magic/version
sample rate
total frames
number of channels
block size
mode
default bit depth
```

Each lossy block stores:

```text
first sample per channel
start step index per channel
bits used per channel
packed audio codes
```

Lossless turbo files store:

```text
main lossless header
compressed lossless payload
PCM CRC32
```

## Advantages

* small file size
* simple decoder for lossy modes
* block-based structure
* supports mono and stereo
* supports lossy and lossless modes
* can be compressed further with zstd
* `.lum8z` supports compressed LUM8 files

## Limitations

* not supported by normal media players yet
* lossy quality depends on preset, block size, and source material
* lossless turbo is larger than lossy modes

Example extensions:

```text
.lum8   = normal LUM8 file
.lum8z  = compressed LUM8 file
```

```bash
python lum8_encoder.py input.flac output.lum8z
```

The zstd-compressed version must be decompressed before normal LUM8 decoding, unless the decoder supports `.lum8z` directly.

## Name

**LUM8** means:

```text
LUM = Low-weight-Universal-Music
8   = Infinity symbol
```

## Copyright

See the copyright file.
