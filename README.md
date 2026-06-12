# LUM8
### Low-weight-Universal-Music-8
(Alias name was NEON.)

**LUM8** stands for **LLow-weight-Universal-Music-8**. It is a new audio format designed to reduce PCM audio size using adaptive 4-bit differential encoding.

LUM8 is **lossy**, meaning the decoded audio is not identical to the original. The goal is small file size with good audible quality.

## Installation

```bash
pip install numpy soundfile numba zstandard
```

## Usage

Encode WAV/FLAC to LUM8:

```bash
python lum8_encoder.py input.flac output.lum8
```

Play the music:

```bash
Open lum8_player.html and use to play.
```



## Short version

LUM8 does not store every audio sample directly. Instead, it stores how the sound changes from the previous sample.

Each new sample is encoded as a 4-bit value called a **nibble**:

```text
1 bit   = direction, up or down
3 bits  = change amount
```

Two samples fit inside one byte:

```text
sample A = 4 bits
sample B = 4 bits
= 1 byte
```

## How it works

1. The input audio is read as WAV/FLAC.
2. The audio is converted to 16-bit PCM.
3. The audio is split into blocks.
4. Each block stores its first sample and starting step level.
5. Instead of storing full samples, the encoder stores the difference from the previous value.
6. Each difference is encoded as a 4-bit nibble.
7. The step size adapts automatically to the movement of the audio.
8. Two 4-bit nibbles are packed into one byte.

## Adaptive step size

Quiet or detailed parts use smaller steps.

Loud or fast-changing parts use larger steps.

This allows the format to follow both soft and strong audio without using full 16-bit samples for every point.

## Smart encoding

The encoder tests all 16 possible nibble values and chooses the one that gives the lowest error.

It also uses a small one-sample lookahead, so it can sometimes choose a slightly less perfect value now if it makes the next sample better.

## File structure

A LUM8 file contains:

```text
main header
block 1 header
block 1 packed nibbles
block 2 header
block 2 packed nibbles
...
```

The main header stores:

```text
magic/version
sample rate
total frames
number of channels
block size
```

Each block stores:

```text
first sample per channel
start step index per channel
packed 4-bit audio data
```

## Advantages

- small file size
- simple decoder
- block-based structure
- supports mono and stereo
- can be compressed further with zstd

## Limitations

- lossy, not bit-perfect
- experimental format
- not supported by normal media players (yet?)
- quality depends on block size and source material

## Optional zstd compression

LUM8 can be compressed further using zstd without changing the decoded sound quality.

Example extensions:

```text
.lum8   = normal LUM8 file
.lum8z  = zstd-compressed LUM8 file
```

```bash
python lum8_encoder.py input.flac output.lum8z
```

The zstd-compressed version must be decompressed before normal LUM8 decoding, unless the decoder supports `.lum8z` directly.

## Name

**LUM8** means:

```text
LUM = Luminous
8   = Infinity symbol / compact micro format
```

Full name:

```text
Low-weight-Universal-Music Infinity (Yes 8 is supposed to be infinity)
```

## Copyright

Look under *copyright*
