# Aligner

Aligner creates timed subtitle files from an audio file and an official
plain-text transcript. It uses `faster-whisper` word timestamps for the audio
timing, then maps the official transcript text onto those timings so the final
captions keep the trusted wording.

The default workflow is batch-friendly: put matching `.mp3` or `.wav` and `.txt`
files in `convert/`, then run `uv run aligner`. Outputs are written to
`aligned/`.

## Requirements

- `uv`
- Python 3.11 is currently used by the local `.python-version`.
- `ffmpeg` must be installed and available on `PATH`.

On macOS:

```sh
brew install ffmpeg
```

The first ASR run may download the selected `faster-whisper` model.

Open-caption burning requires an ffmpeg build with the `subtitles` filter. On
macOS with Homebrew, install `ffmpeg-full` if the regular build does not include
that filter:

```sh
brew install ffmpeg-full
```

`burn-subtitles` automatically checks common Homebrew `ffmpeg-full` locations
when regular `ffmpeg` does not support subtitles. You can also point directly to
a custom ffmpeg binary:

```sh
ALIGNER_FFMPEG=/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg uv run burn-subtitles input.mp4 captions.srt output.mp4
```

Verify subtitle-filter support with:

```sh
/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg -filters | grep subtitles
```

## Quick Start

Install and sync the Python environment:

```sh
uv sync
```

Put matching files in `convert/`:

```text
convert/
  20260812_DPB.mp3
  20260812_DPB.txt
```

Run the aligner:

```sh
uv run aligner
```

That writes:

```text
aligned/
  20260812_DPB.srt
  20260812_DPB.vtt
  20260812_DPB.timing-report.json
```

The `.srt` and `.vtt` files use the official transcript text. The timing report
shows how many official words matched ASR word anchors and lists weak spans
where timing had to be estimated.

## Batch Mode

By default, `aligner` scans the top level of `convert/` for `.mp3` and `.wav`
files. Each audio file needs a same-stem `.txt` transcript:

```text
interview.mp3
interview.txt
```

Audio files without matching transcripts are skipped. To use different folders:

```sh
uv run aligner --input-dir path/to/input --output-dir path/to/output
```

## One File Pair

To process one specific audio/transcript pair:

```sh
uv run aligner convert/interview.mp3 convert/interview.txt
```

This writes to `aligned/` by default. You can override individual output paths:

```sh
uv run aligner convert/interview.mp3 convert/interview.txt \
  --srt aligned/interview.srt \
  --vtt aligned/interview.vtt \
  --report aligned/interview.timing-report.json
```

## Caption Timing Options

The default model is `base.en`, which is a sturdy speed/accuracy balance for
English audio:

```sh
uv run aligner --model base.en
uv run aligner --model small.en
```

Useful tuning options:

```sh
uv run aligner --max-caption-chars 70 --max-caption-duration 4
uv run aligner --beam-size 3
uv run aligner --language en
```

Larger models and bigger beam sizes may improve difficult audio, but they take
longer.

## QA Report

Every `aligner` run writes a timing report. Look at:

- `matched_ratio`: higher means more official words were directly anchored to
  ASR word timings.
- `weak_span_count`: number of longer stretches where timings were estimated.
- `weak_spans`: the actual text and timestamps to review.

The separate `check-transcript` command is available when you want a diagnostic
comparison without writing retimed captions:

```sh
uv run check-transcript convert/interview.mp3 convert/interview.txt
```

Write a JSON report and raw ASR timing SRT:

```sh
uv run check-transcript convert/interview.mp3 convert/interview.txt \
  --output aligned/interview.qa.json \
  --asr-srt aligned/interview.asr.srt
```

## Burning Open Captions

After generating an SRT file, burn it into a video as open captions:

```sh
uv run burn-subtitles path/to/input.mp4 path/to/captions.srt path/to/output.mp4
```

By default, `burn-subtitles` creates a quick 720p review file for checking
caption timing:

```text
quick:  720p max height, ultrafast encode, CRF 30
medium: source height, medium encode, CRF 23
high:   source height, slow encode, CRF 18
```

Use named profiles for better exports:

```sh
uv run burn-subtitles input.mp4 captions.srt output.mp4 --quality medium
uv run burn-subtitles input.mp4 captions.srt output.mp4 --quality high
```

Override profile defaults when needed:

```sh
uv run burn-subtitles input.mp4 captions.srt output.mp4 --quality quick --crf 28
uv run burn-subtitles input.mp4 captions.srt output.mp4 --quality medium --height 720
uv run burn-subtitles input.mp4 captions.srt output.mp4 --no-progress
```

Existing output videos are not overwritten unless you pass `--force`:

```sh
uv run burn-subtitles input.mp4 captions.srt output.mp4 --force
```

## Tests

Run the test suite with:

```sh
uv run pytest
```

## Current Limitations

- The aligner scans only the top level of the input directory.
- Audio files must currently be `.mp3` or `.wav`.
- The official transcript remains the source of truth, but transcript/audio
  differences can still create estimated timing spans that need review.
- `faster-whisper` may download model files on first use.
- Burning open captions requires an ffmpeg build with the `subtitles` filter.

See `DEVELOPMENT.md` for maintainer notes and the suggested next cleanup pass.
