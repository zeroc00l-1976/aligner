# Development Notes

This repository is a starter forced-alignment utility. Before changing behavior,
use this document as the shared map of what exists, what is assumed, and what
needs cleanup.

## Project Shape

```text
align.py          Main CLI and alignment implementation
pyproject.toml    Minimal project metadata
uv.lock           Locked uv dependency resolution
.python-version   Local Python version, currently 3.11
.venv/            Local virtual environment, ignored by Git
convert/          Default local input folder
aligned/          Default generated output folder, ignored by Git
```

There is currently no Git repository initialized in this folder.

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

`aeneas` needs `numpy` available while it builds, so `pyproject.toml` includes a
`tool.uv.extra-build-dependencies` entry for it. Keep that setting unless the
dependency is replaced or a future `aeneas` release fixes its build metadata.

The project installs a console command named `aligner`.

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

## Known Issues And Risks

- **Large local media:** raw audio files in `convert/` can be very large and are
  ignored by Git.
- **Generated outputs:** `aligned/` contains generated artifacts and is ignored
  by Git.
- **No recursive processing:** nested input directories are ignored.
- **Basic test coverage only:** tests cover pure formatting/conversion behavior
  and CLI validation, but not full aeneas alignment.

## Suggested Next Pass

1. Add an integration fixture for a very short audio/transcript pair.
2. Consider recursive input processing if real workflows need nested folders.
3. Consider moving from a single-file module to a package if the code grows.

## Verification Commands

Basic environment check:

```sh
uv sync
uv run aligner --help
uv run pytest
ffmpeg -version
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
