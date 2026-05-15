# Development Notes

This repository is a starter forced-alignment utility. Before changing behavior,
use this document as the shared map of what exists, what is assumed, and what
needs cleanup.

## Project Shape

```text
align.py          Main CLI and alignment implementation
burn.py           Helper CLI for burning SRT captions into video
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

Because `ffmpeg-full` is keg-only, either add it to `PATH` before the regular
ffmpeg:

```sh
export PATH="/opt/homebrew/opt/ffmpeg-full/bin:$PATH"
```

or set `ALIGNER_FFMPEG` when running a command:

```sh
ALIGNER_FFMPEG=/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg uv run burn-subtitles input.mp4 captions.srt output.mp4
```

Both `align.py` and `burn.py` respect `ALIGNER_FFMPEG`.

`aeneas` needs `numpy` available while it builds, so `pyproject.toml` includes a
`tool.uv.extra-build-dependencies` entry for it. Keep that setting unless the
dependency is replaced or a future `aeneas` release fixes its build metadata.

The project installs two console commands:

```sh
uv run aligner
uv run burn-subtitles
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
- `cli()`: parses CLI arguments for the `burn-subtitles` command.

## Runtime Flow

1. Parse `--input-dir`, `--output-dir`, and `--language`.
2. If both positional file arguments are provided, align that single pair.
3. If no positional file arguments are provided, run batch mode over the input
   folder.
4. In batch mode, find top-level `.wav` and `.mp3` files in the input directory.
5. For each audio file, look for a same-stem `.txt` transcript.
6. Refuse to overwrite existing outputs unless `--force` is passed.
7. Convert non-WAV audio to temporary WAV.
8. Run aeneas and write JSON.
9. Convert JSON to SRT and VTT.

## Open Caption Flow

1. Pass an input video, `.srt` file, and output video path to `burn-subtitles`.
2. Check that ffmpeg is installed and exposes the `subtitles` filter.
3. Refuse to overwrite the output unless `--force` is passed.
4. Run ffmpeg with the subtitles filter and copy the audio stream.

## Known Issues And Risks

- **Large local media:** raw audio files in `convert/` can be very large and are
  ignored by Git.
- **Generated outputs:** `aligned/` contains generated artifacts and is ignored
  by Git.
- **No recursive processing:** nested input directories are ignored.
- **Basic test coverage only:** tests cover pure formatting/conversion behavior
  and CLI validation, but not full aeneas alignment.
- **ffmpeg subtitle support varies:** open-caption burning requires an ffmpeg
  build with the `subtitles` filter, which is not present in every install.

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
