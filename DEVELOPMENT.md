# Development Notes

This repository is a starter caption alignment utility. The current trusted path
is ASR word timing plus official transcript text; the old aeneas console
workflow has been retired from the installed commands.

## Project Shape

```text
align.py          Legacy/shared helpers and subtitle formatting utilities
burn.py           Helper CLI for burning SRT captions into video
qa.py             ASR retiming and transcript QA commands
pyproject.toml    Project metadata and console scripts
uv.lock           Locked uv dependency resolution
.python-version   Local Python version, currently 3.11
.venv/            Local virtual environment, ignored by Git
convert/          Default local input folder, ignored by Git
aligned/          Default generated output folder, ignored by Git
tests/            Pytest coverage for fast non-audio behavior
```

The repository uses `main` as its default branch.

## Local Environment

This project is managed with `uv`:

```sh
uv sync
uv run aligner --help
```

Known local versions at the time this was written:

```text
Python: 3.11.3 in .venv
ffmpeg: 8.1
```

Regular Homebrew `ffmpeg` may not include the `subtitles` filter. For
open-caption burning on macOS, install `ffmpeg-full`:

```sh
brew install ffmpeg-full
```

Because `ffmpeg-full` is keg-only, it is not linked over the regular `ffmpeg`.
`burn-subtitles` handles the common macOS case automatically by checking these
paths when regular `ffmpeg` lacks the `subtitles` filter:

```text
/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg
/usr/local/opt/ffmpeg-full/bin/ffmpeg
```

For custom installs, either add the subtitle-capable ffmpeg to `PATH` before the
regular ffmpeg:

```sh
export PATH="/opt/homebrew/opt/ffmpeg-full/bin:$PATH"
```

or set `ALIGNER_FFMPEG` when running a command:

```sh
ALIGNER_FFMPEG=/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg uv run burn-subtitles input.mp4 captions.srt output.mp4
```

## Console Commands

```sh
uv run aligner
uv run burn-subtitles
uv run check-transcript
```

`aligner` is the production caption path. It scans `convert/` by default,
creates official-text SRT/VTT files, and writes a timing QA report.

`check-transcript` is comparison-only QA. It does not produce final captions.

## Test Suite

Pytest is installed as a `uv` development dependency in `pyproject.toml`:

```toml
[dependency-groups]
dev = [
    "pytest>=8.4.2",
]
```

Run tests with:

```sh
uv run pytest
```

Current coverage includes:

- SRT and WebVTT timestamp formatting.
- JSON-to-SRT and JSON-to-VTT conversion for legacy helpers.
- Supported audio file discovery.
- Batch and single-pair retiming path helpers.
- Burn command construction and ffmpeg subtitle-filter preflight behavior.
- ASR QA text normalization, similarity scoring, report generation, and ASR SRT
  serialization.

The tests intentionally avoid full audio transcription so they stay fast.

## Code Map

`qa.py` is organized around these functions:

- `transcribe_words(...)`: runs faster-whisper with word timestamps.
- `official_word_tokens(...)`: tokenizes the official transcript while
  preserving original text.
- `transfer_word_timings(...)`: maps ASR word timings onto official words.
- `build_captions_from_timed_words(...)`: groups timed official words into
  readable subtitle cues.
- `retime_official_transcript(...)`: orchestrates the official-text caption
  generation flow.
- `timing_report(...)`: summarizes matched versus estimated word timings and
  weak spans.
- `retime_cli(...)`: parses CLI arguments for `aligner`.
- `compare_asr_to_official(...)`: comparison-only QA for suspicious transcript
  differences.
- `cli(...)`: parses CLI arguments for `check-transcript`.

`burn.py` is organized around these functions:

- `subtitles_filter(subtitle_path, style)`: builds the ffmpeg subtitles filter.
- `require_subtitles_filter()`: checks that ffmpeg includes the required
  `subtitles` filter.
- `build_ffmpeg_command(...)`: builds the ffmpeg command for open captions.
- `burn_subtitles(...)`: validates paths and runs ffmpeg.
- `probe_video_info(...)`: reads source video specs for user-facing context.
- `progress_bar(...)`: renders terminal progress from ffmpeg progress events.
- `cli()`: parses CLI arguments for `burn-subtitles`.

`align.py` still contains legacy/shared helpers:

- `seconds_to_srt_time(t)` and `seconds_to_vtt_time(t)`.
- `transcript_caption_lines(...)`.
- Legacy aeneas alignment functions that are no longer wired to the `aligner`
  console command.

## Aligner Runtime Flow

1. Parse optional positional `audio` and `transcript` paths.
2. If both are provided, process that single file pair.
3. If neither is provided, scan `--input-dir` for top-level `.mp3` and `.wav`
   files with same-stem `.txt` transcripts.
4. Transcribe audio with faster-whisper word timestamps.
5. Tokenize the official transcript.
6. Match ASR words to official transcript words.
7. Transfer ASR timestamps onto matching official words.
8. Estimate timing for official-only words between matched anchors.
9. Build readable caption cues.
10. Write official-text `.srt`, `.vtt`, and `.timing-report.json` files.

Batch example:

```sh
uv run aligner
```

Single-pair example:

```sh
uv run aligner convert/audio.mp3 convert/audio.txt \
  --srt aligned/audio.srt \
  --vtt aligned/audio.vtt \
  --report aligned/audio.timing-report.json
```

## Open Caption Flow

1. Pass an input video, `.srt` file, and output video path to `burn-subtitles`.
2. Find an ffmpeg binary with the `subtitles` filter: `ALIGNER_FFMPEG`, then
   regular `ffmpeg`, then common Homebrew `ffmpeg-full` paths.
3. Probe source video specs and print them before encoding.
4. Refuse to overwrite the output unless `--force` is passed.
5. Apply a named quality profile. `quick` is the default review output; `medium`
   and `high` keep source height with slower/better encoding defaults.
6. Run ffmpeg with the subtitles filter, explicit x264 settings, copied audio,
   and `-progress pipe:1`.
7. Parse ffmpeg progress events and update a terminal progress bar.

## Known Issues And Risks

- **Large local media:** raw files in `convert/` can be very large and are
  ignored by Git.
- **Generated outputs:** `aligned/` contains generated artifacts and is ignored
  by Git.
- **No recursive processing:** nested input directories are ignored.
- **Estimated timing spans:** transcript/audio differences can create weak
  spans. The timing report makes those reviewable, but it cannot make mismatched
  source material perfect automatically.
- **ASR model downloads:** faster-whisper may download model files on first use.
- **ffmpeg subtitle support varies:** open-caption burning requires an ffmpeg
  build with the `subtitles` filter.

## Verification Commands

Basic environment check:

```sh
uv sync
uv run aligner --help
uv run burn-subtitles --help
uv run check-transcript --help
uv run pytest
ffmpeg -version
/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg -filters | grep subtitles
```

Run the default batch alignment:

```sh
uv run aligner
```

Inspect generated files:

```sh
ls -lh aligned
sed -n '1,40p' aligned/20260812_DPB.srt
sed -n '1,80p' aligned/20260812_DPB.timing-report.json
```

## Suggested Next Pass

1. Add an integration fixture for a very short audio/transcript pair.
2. Consider recursive input processing if real workflows need nested folders.
3. Consider moving from single-file modules to a package if the code grows.
