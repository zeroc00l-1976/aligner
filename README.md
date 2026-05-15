# Aligner

Aligner is a small command-line tool for forced-aligning audio with a matching
plain-text transcript. It uses `aeneas` to produce a JSON sync map, then converts
that sync map into subtitle files in SRT and WebVTT format.

The current project is intentionally starter-sized and managed with `uv`: the
main implementation is in `align.py`, with `convert/` as the default input
folder and `aligned/` as the default output folder.

## What It Does

For each `.wav` or `.mp3` file in the input directory, the script looks for a
transcript with the same base filename:

```text
convert/
  20250805_BPB.wav
  20250805_BPB.txt
```

Running the aligner writes:

```text
aligned/
  20250805_BPB.json
  20250805_BPB.srt
  20250805_BPB.vtt
```

## Requirements

- `uv`
- Python 3.11 is currently used by the local `.python-version`.
- `ffmpeg` must be installed and available on `PATH`.

On macOS, install `ffmpeg` with:

```sh
brew install ffmpeg
```

## Quick Start

Install and sync the Python environment:

```sh
uv sync
```

Run the default alignment job:

```sh
uv run aligner
```

By default, this reads from `convert/` and writes to `aligned/`.

## Using Your Own Files

1. Put your audio file in `convert/`.
2. Put a plain-text transcript with the same base filename in `convert/`.
3. Run the aligner.
4. Find the generated files in `aligned/`.

Example input:

```text
convert/
  interview.wav
  interview.txt
```

Run:

```sh
uv run aligner
```

Example output:

```text
aligned/
  interview.json
  interview.srt
  interview.vtt
```

The script processes every top-level `.wav` and `.mp3` file in the input
directory. If an audio file does not have a matching `.txt` transcript, it is
skipped and the script continues with the next file.

Existing output files are not overwritten unless you pass `--force`:

```sh
uv run aligner --force
```

## Aligning One File Pair

To align one specific audio/transcript pair instead of processing the whole
input folder, pass both paths:

```sh
uv run aligner path/to/interview.wav path/to/interview.txt
```

This still writes to `aligned/` by default. To choose a different output folder:

```sh
uv run aligner path/to/interview.wav path/to/interview.txt --output-dir path/to/output
```

To use custom folders:

```sh
uv run aligner --input-dir path/to/input --output-dir path/to/output
```

To set the aeneas language code:

```sh
uv run aligner --language eng
```

## Input Rules

- Audio files must end in `.wav` or `.mp3`.
- Each audio file must have a same-stem `.txt` transcript in the same input
  directory.
- Transcripts are treated as plain text by aeneas.
- Blank transcript fragments can appear in the JSON output, but are skipped when
  generating SRT and VTT.

Example:

```text
interview.wav
interview.txt
```

The transcript should already be cleaned into the chunks you want aligned. Each
line or paragraph can become a subtitle fragment depending on how aeneas reads
the plain-text file.

## Output Formats

- `.json`: raw aeneas sync map.
- `.srt`: numbered subtitle cues using comma millisecond separators.
- `.vtt`: WebVTT cues with a `WEBVTT` header.

## Burning Open Captions

After generating an SRT file, you can burn it into a video as open captions with
the helper command:

```sh
uv run burn-subtitles path/to/input.mp4 path/to/captions.srt path/to/output.mp4
```

This creates a new video file with the captions rendered into the image. The
original audio stream is copied when possible.

Existing output videos are not overwritten unless you pass `--force`:

```sh
uv run burn-subtitles path/to/input.mp4 path/to/captions.srt path/to/output.mp4 --force
```

The helper uses ffmpeg's `subtitles` video filter, so your ffmpeg build must
include libass/subtitles support. Check with:

```sh
ffmpeg -filters | grep subtitles
```

To use ffmpeg's default subtitle styling:

```sh
uv run burn-subtitles path/to/input.mp4 path/to/captions.srt path/to/output.mp4 --no-style
```

## Tests

Run the test suite with:

```sh
uv run pytest
```

The current tests cover fast behavior that does not need a real audio alignment
run: timestamp formatting, JSON-to-SRT/VTT conversion, audio file discovery,
overwrite handling, burn command construction, and CLI validation.

## Current Limitations

- The script only scans the top level of the input directory.
- Existing output files are skipped unless `--force` is passed.
- MP3 files are converted to temporary 16 kHz mono WAV files before alignment.
- Tests do not yet cover a full aeneas alignment against a small audio fixture.
- Burning open captions requires an ffmpeg build with the `subtitles` filter.

See `DEVELOPMENT.md` for maintainer notes and the suggested next cleanup pass.
