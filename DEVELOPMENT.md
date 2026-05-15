# Development Notes

This repository is a starter forced-alignment utility. Before changing behavior,
use this document as the shared map of what exists, what is assumed, and what
needs cleanup.

## Project Shape

```text
align.py          Main CLI and alignment implementation
burn.py           Helper CLI for burning SRT captions into video
qa.py             Optional ASR comparison CLI for transcript QA
pyproject.toml    Minimal project metadata
uv.lock           Locked uv dependency resolution
.python-version   Local Python version, currently 3.11
.venv/            Local virtual environment, ignored by Git
convert/          Default local input folder
aligned/          Default generated output folder, ignored by Git
tests/            Pytest coverage for fast non-audio behavior
```

The repository uses `main` as its default branch.

## Local Environment

This project is managed with `uv`. Use:

```sh
uv sync
uv run aligner --help
```

Known local versions at the time this was written:

```text
Python: 3.11.3 in .venv
ffmpeg: 8.1
aeneas: 1.7.3.0
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

Both `align.py` and `burn.py` respect `ALIGNER_FFMPEG`; `burn.py` also performs
the automatic `ffmpeg-full` fallback above.

`aeneas` needs `numpy` available while it builds, so `pyproject.toml` includes a
`tool.uv.extra-build-dependencies` entry for it. Keep that setting unless the
dependency is replaced or a future `aeneas` release fixes its build metadata.

The project installs two console commands:

```sh
uv run aligner
uv run burn-subtitles
uv run --group qa check-transcript
uv run --group qa retime-transcript
```

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

The tests live in `tests/test_align.py`. They intentionally avoid the expensive
or environment-sensitive aeneas path for now, so they run quickly and can be used
as a safety net while refactoring.

Current coverage includes:

- SRT and WebVTT timestamp formatting.
- Joining non-empty fragment lines.
- JSON-to-SRT and JSON-to-VTT conversion.
- Supported audio file discovery.
- Output path and overwrite checks.
- CLI validation for batch mode, skipped existing outputs, and incomplete file
  pairs.
- Burn command construction and ffmpeg subtitle-filter preflight behavior.
- ASR QA text normalization, similarity scoring, report generation, and ASR SRT
  serialization.

## Code Map

`align.py` is organized around these functions:

- `seconds_to_srt_time(t)`: formats float seconds as `HH:MM:SS,mmm`.
- `seconds_to_vtt_time(t)`: formats float seconds as `HH:MM:SS.mmm`.
- `require_ffmpeg()`: checks that `ffmpeg -version` succeeds.
- `load_aeneas()`: imports aeneas only when alignment actually runs.
- `convert_to_wav(input_audio, tmp_dir)`: converts audio to 16 kHz mono PCM WAV.
- `run_alignment(audio_path, transcript_path, output_dir, language)`: configures
  and runs aeneas, then writes the JSON sync map.
- `read_fragments(json_path)`: reads and validates the aeneas fragment list.
- `fragment_text(fragment)`: joins non-empty text lines in a fragment.
- `prepare_transcript_for_alignment(...)`: writes a temporary caption-sized
  transcript for aeneas so alignment happens against short chunks.
- `caption_cues_for_fragment(...)`: fallback splitter for any long aeneas
  fragments that remain after pre-splitting.
- `json_to_srt(json_path, srt_path)`: converts aeneas JSON fragments to SRT.
- `json_to_vtt(json_path, vtt_path)`: converts aeneas JSON fragments to WebVTT.
- `find_audio_files(input_dir)`: returns sorted top-level `.wav` and `.mp3`
  files.
- `cli()`: parses CLI arguments and coordinates batch processing.
- `main()`: console-script wrapper around `cli()`.

`burn.py` is organized around these functions:

- `subtitles_filter(subtitle_path, style)`: builds the ffmpeg subtitles filter.
- `require_subtitles_filter()`: checks that ffmpeg includes the required
  `subtitles` filter.
- `build_ffmpeg_command(...)`: builds the ffmpeg command for open captions.
- `burn_subtitles(...)`: validates paths and runs ffmpeg.
- `probe_video_info(...)`: reads source video specs for user-facing context.
- `progress_bar(...)`: renders simple terminal progress from ffmpeg progress
  events.
- `cli()`: parses CLI arguments for the `burn-subtitles` command.

`qa.py` is organized around these functions:

- `transcribe_audio(...)`: runs faster-whisper and returns timestamped ASR
  segments.
- `transcribe_words(...)`: runs faster-whisper with word timestamps for retiming.
- `retime_official_transcript(...)`: maps official transcript words onto ASR
  word timings and produces official-text captions.
- `timing_report(...)`: summarizes matched versus estimated word timings and
  weak spans.
- `compare_asr_to_official(...)`: compares ASR segments with nearby official
  transcript chunks and records low-score mismatches.
- `write_asr_srt(...)`: writes the raw ASR timing output for human review.
- `cli()`: parses CLI arguments for `check-transcript`.

## Runtime Flow

1. Parse `--input-dir`, `--output-dir`, and `--language`.
2. If both positional file arguments are provided, align that single pair.
3. If no positional file arguments are provided, run batch mode over the input
   folder.
4. In batch mode, find top-level `.wav` and `.mp3` files in the input directory.
5. For each audio file, look for a same-stem `.txt` transcript.
6. Pre-split the transcript into caption-sized lines unless `--no-pre-split` is
   passed.
7. Refuse to overwrite existing outputs unless `--force` is passed.
8. Convert non-WAV audio to temporary WAV.
9. Run aeneas against the caption-sized transcript and write JSON.
10. Convert JSON to SRT and VTT, with a proportional fallback split for any
   remaining long fragments.

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

## Retiming And QA Flow

`retime-transcript` is the preferred high-accuracy caption path when timing must
follow the audio closely while text must remain official.

1. Load faster-whisper from the optional `qa` dependency group.
2. Transcribe the audio/video with word timestamps.
3. Tokenize the official transcript.
4. Match ASR words to official words.
5. Transfer ASR word timestamps onto matching official words.
6. Estimate timing for official-only words between matched anchors.
7. Write official-text SRT/VTT captions.
8. Write a timing QA report with weak spans and matched-word ratio.

Run it with:

```sh
uv run --group qa retime-transcript audio.mp3 transcript.txt \
  --srt aligned/audio.srt \
  --vtt aligned/audio.vtt \
  --report aligned/audio.timing-report.json
```

`check-transcript` remains available as a comparison-only QA layer.

1. Load faster-whisper from the optional `qa` dependency group.
2. Transcribe the audio/video into timestamped ASR segments.
3. Split the official transcript with the same caption chunker used by
   `aligner`.
4. Compare ASR segments against nearby official chunks with a similarity score.
5. Print suspicious low-score segments and optionally write:
   - JSON report with all matches and mismatches.
   - Raw ASR SRT for timing review.

Run it with:

```sh
uv run --group qa check-transcript audio.mp3 transcript.txt \
  --output aligned/audio.qa.json \
  --asr-srt aligned/audio.asr.srt
```

## Known Issues And Risks

- **Large local media:** raw audio files in `convert/` can be very large and are
  ignored by Git.
- **Generated outputs:** `aligned/` contains generated artifacts and is ignored
  by Git.
- **No recursive processing:** nested input directories are ignored.
- **Basic test coverage only:** tests cover pure formatting/conversion behavior
  and CLI validation, but not full aeneas alignment.
- **Estimated fallback timing:** pre-splitting should make most aeneas fragments
  subtitle-sized, but any remaining long fragment is split proportionally. This
  is a fallback, not true word-level alignment.
- **ffmpeg subtitle support varies:** open-caption burning requires an ffmpeg
  build with the `subtitles` filter, which is not present in every install.
- **ASR QA is advisory:** faster-whisper can flag likely transcript/audio
  mismatches, but the official transcript remains the source of truth.
- **Retimed captions still need review:** matched ASR words provide the best
  timing anchors; estimated weak spans are called out in the timing report.
- **Aligner progress is file-level:** aeneas does not expose fine-grained
  per-file alignment progress, so `aligner` reports batch progress after each
  file rather than percentage inside one long file.

## Suggested Next Pass

1. Add an integration fixture for a very short audio/transcript pair.
2. Consider recursive input processing if real workflows need nested folders.
3. Consider moving from a single-file module to a package if the code grows.

## Verification Commands

Basic environment check:

```sh
uv sync
uv run aligner --help
uv run burn-subtitles --help
uv run --group qa check-transcript --help
uv run --group qa retime-transcript --help
uv run pytest
ffmpeg -version
/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg -filters | grep subtitles
```

Run the sample alignment:

```sh
uv run aligner --input-dir convert --output-dir aligned --language eng
```

Inspect generated files:

```sh
ls -lh aligned
sed -n '1,40p' aligned/20250805_BPB.srt
sed -n '1,40p' aligned/20250805_BPB.vtt
```

## Notes For Future Refactoring

Keep the first refactor narrow. The code already has separable pure formatting
and conversion functions, so tests can land before larger changes. The riskiest
parts are environment setup and `aeneas` execution, not the subtitle writers.
