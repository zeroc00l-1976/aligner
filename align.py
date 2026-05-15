#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


TIME_PRECISION = 1000
AUDIO_EXTENSIONS = {".wav", ".mp3"}
OUTPUT_EXTENSIONS = (".json", ".srt", ".vtt")
FFMPEG_ENV_VAR = "ALIGNER_FFMPEG"
PROGRESS_BAR_WIDTH = 30
DEFAULT_MAX_CAPTION_CHARS = 84
DEFAULT_MAX_CAPTION_DURATION = 6.0
MIN_CAPTION_DURATION = 0.5


def ffmpeg_binary() -> str:
    return os.environ.get(FFMPEG_ENV_VAR, "ffmpeg")


def progress_bar(current: int, total: int, width: int = PROGRESS_BAR_WIDTH) -> str:
    if total <= 0:
        return f"[{'?' * width}]"
    ratio = min(1.0, max(0.0, current / total))
    filled = int(width * ratio)
    return f"[{'#' * filled}{'.' * (width - filled)}]"


def print_batch_progress(current: int, total: int) -> None:
    percent = 100.0 if total <= 0 else (min(total, current) / total) * 100
    print(f"      Batch progress {progress_bar(current, total)} {current}/{total} ({percent:5.1f}%)")


def seconds_to_srt_time(t: float) -> str:
    total_millis = max(0, int((t * TIME_PRECISION) + 0.5))
    total_seconds, millis = divmod(total_millis, TIME_PRECISION)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def seconds_to_vtt_time(t: float) -> str:
    total_millis = max(0, int((t * TIME_PRECISION) + 0.5))
    total_seconds, millis = divmod(total_millis, TIME_PRECISION)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def require_ffmpeg() -> None:
    try:
        subprocess.run([ffmpeg_binary(), "-version"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        raise RuntimeError(
            "ffmpeg is required to handle mp3 input and for reliable audio decoding. "
            f"Install it with Homebrew and ensure it is on PATH, or set {FFMPEG_ENV_VAR}."
        ) from e


def load_aeneas() -> tuple[type[Any], type[Any]]:
    try:
        from aeneas.executetask import ExecuteTask
        from aeneas.task import Task
    except ModuleNotFoundError as e:
        raise RuntimeError(
            "aeneas is required for alignment. Install project dependencies with: uv sync"
        ) from e

    return ExecuteTask, Task


def convert_to_wav(input_audio: Path, tmp_dir: Path) -> Path:
    """
    Convert input audio to 16kHz mono PCM wav for aeneas compatibility.
    Returns the path to the converted wav.
    """
    require_ffmpeg()

    out_wav = tmp_dir / f"{input_audio.stem}.aeneas.wav"

    cmd = [
        ffmpeg_binary(),
        "-y",
        "-i",
        str(input_audio),
        "-ac",
        "1",           # mono
        "-ar",
        "16000",       # 16 kHz
        "-c:a",
        "pcm_s16le",   # PCM 16-bit
        str(out_wav),
    ]

    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed converting {input_audio.name} to wav:\n{proc.stderr}")

    return out_wav


def run_alignment(audio_path: Path, transcript_path: Path, output_dir: Path, language: str = "eng") -> Path:
    """
    Run aeneas alignment and output JSON sync map.
    Returns path to the generated JSON file.
    """
    audio_path = audio_path.resolve()
    transcript_path = transcript_path.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not audio_path.exists():
        raise FileNotFoundError(f"Audio not found: {audio_path}")
    if not transcript_path.exists():
        raise FileNotFoundError(f"Transcript not found: {transcript_path}")

    execute_task, task_type = load_aeneas()

    config = (
        f"task_language={language}|"
        "is_text_type=plain|"
        "os_task_file_format=json|"
        "is_audio_file_detect_head_tail=true"
    )

    task = task_type(config_string=config)

    task.audio_file_path_absolute = str(audio_path)
    task.text_file_path_absolute = str(transcript_path)

    json_path = output_dir / f"{audio_path.stem}.json"
    task.sync_map_file_path_absolute = str(json_path)

    execute_task(task).execute()
    task.output_sync_map_file()

    return json_path


def fragment_text(fragment: dict[str, Any]) -> str:
    return " ".join(line.strip() for line in fragment.get("lines", []) if line.strip())


def split_text_for_captions(text: str, max_chars: int = DEFAULT_MAX_CAPTION_CHARS) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []
    if max_chars <= 0 or len(normalized) <= max_chars:
        return [normalized]

    chunks: list[str] = []
    current_words: list[str] = []
    sentence_end_re = re.compile(r"[.!?][\"')\]]?$")

    for word in normalized.split():
        candidate_words = [*current_words, word]
        candidate = " ".join(candidate_words)

        if current_words and len(candidate) > max_chars:
            chunks.append(" ".join(current_words))
            current_words = [word]
            continue

        current_words = candidate_words
        if len(candidate) >= max_chars * 0.6 and sentence_end_re.search(word):
            chunks.append(candidate)
            current_words = []

    if current_words:
        chunks.append(" ".join(current_words))

    return chunks


def split_text_into_count(text: str, count: int) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    words = normalized.split()
    if not words or count <= 1:
        return [normalized] if normalized else []

    count = min(count, len(words))
    chunks: list[str] = []
    start = 0
    for index in range(count):
        remaining_words = len(words) - start
        remaining_chunks = count - index
        size = max(1, round(remaining_words / remaining_chunks))
        chunks.append(" ".join(words[start : start + size]))
        start += size
    return [chunk for chunk in chunks if chunk]


def caption_chunks_for_text(
    text: str,
    duration: float,
    max_chars: int = DEFAULT_MAX_CAPTION_CHARS,
    max_duration: float = DEFAULT_MAX_CAPTION_DURATION,
) -> list[str]:
    if max_chars <= 0 and max_duration <= 0:
        return [text] if text else []

    by_text = split_text_for_captions(text, max_chars=max_chars)
    if not by_text:
        return []

    if max_duration <= 0:
        return by_text

    duration_count = max(1, int((duration / max_duration) + 0.999))
    target_count = max(len(by_text), duration_count)
    if target_count <= len(by_text):
        return by_text

    return split_text_into_count(text, target_count)


def caption_cues_for_fragment(
    fragment: dict[str, Any],
    max_chars: int = DEFAULT_MAX_CAPTION_CHARS,
    max_duration: float = DEFAULT_MAX_CAPTION_DURATION,
) -> list[tuple[float, float, str]]:
    begin = float(fragment["begin"])
    end = float(fragment["end"])
    text = fragment_text(fragment)
    if not text:
        return []

    duration = max(end - begin, MIN_CAPTION_DURATION)
    chunks = caption_chunks_for_text(text, duration, max_chars=max_chars, max_duration=max_duration)
    if not chunks:
        return []

    total_words = sum(max(1, len(chunk.split())) for chunk in chunks)
    cues: list[tuple[float, float, str]] = []
    cursor = begin

    for index, chunk in enumerate(chunks):
        if index == len(chunks) - 1:
            cue_end = max(end, cursor + MIN_CAPTION_DURATION)
        else:
            word_share = max(1, len(chunk.split())) / total_words
            cue_end = cursor + (duration * word_share)
            cue_end = min(cue_end, end)
            cue_end = max(cue_end, cursor + MIN_CAPTION_DURATION)
        cues.append((cursor, cue_end, chunk))
        cursor = cue_end

    return cues


def transcript_caption_lines(transcript_path: Path, max_chars: int = DEFAULT_MAX_CAPTION_CHARS) -> list[str]:
    raw_text = transcript_path.read_text(encoding="utf-8")
    paragraphs = [paragraph.strip() for paragraph in re.split(r"\n\s*\n", raw_text) if paragraph.strip()]
    lines: list[str] = []
    for paragraph in paragraphs:
        lines.extend(split_text_for_captions(paragraph, max_chars=max_chars))
    return lines


def prepare_transcript_for_alignment(
    transcript_path: Path,
    tmp_dir: Path,
    max_chars: int = DEFAULT_MAX_CAPTION_CHARS,
) -> tuple[Path, int]:
    lines = transcript_caption_lines(transcript_path, max_chars=max_chars)
    if not lines:
        raise ValueError(f"Transcript contains no usable text: {transcript_path}")

    prepared_path = tmp_dir / f"{transcript_path.stem}.caption_chunks.txt"
    prepared_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return prepared_path, len(lines)


def read_fragments(json_path: Path) -> list[dict[str, Any]]:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    fragments = data.get("fragments", [])
    if not isinstance(fragments, list):
        raise ValueError(f"Invalid aeneas JSON, expected 'fragments' list: {json_path}")
    return fragments


def json_to_srt(
    json_path: Path,
    srt_path: Path,
    max_chars: int = DEFAULT_MAX_CAPTION_CHARS,
    max_duration: float = DEFAULT_MAX_CAPTION_DURATION,
) -> None:
    lines = []
    idx = 1
    for frag in read_fragments(json_path):
        for begin, end, text in caption_cues_for_fragment(frag, max_chars=max_chars, max_duration=max_duration):
            lines.append(str(idx))
            lines.append(f"{seconds_to_srt_time(begin)} --> {seconds_to_srt_time(end)}")
            lines.append(text)
            lines.append("")
            idx += 1

    srt_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def json_to_vtt(
    json_path: Path,
    vtt_path: Path,
    max_chars: int = DEFAULT_MAX_CAPTION_CHARS,
    max_duration: float = DEFAULT_MAX_CAPTION_DURATION,
) -> None:
    lines = ["WEBVTT", ""]
    for frag in read_fragments(json_path):
        for begin, end, text in caption_cues_for_fragment(frag, max_chars=max_chars, max_duration=max_duration):
            lines.append(f"{seconds_to_vtt_time(begin)} --> {seconds_to_vtt_time(end)}")
            lines.append(text)
            lines.append("")

    vtt_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def find_audio_files(input_dir: Path) -> list[Path]:
    files = [p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS]
    files.sort()
    return files


def output_paths(output_dir: Path, stem: str) -> tuple[Path, Path, Path]:
    return tuple(output_dir / f"{stem}{ext}" for ext in OUTPUT_EXTENSIONS)


def ensure_outputs_writable(paths: tuple[Path, ...], overwrite: bool) -> None:
    if overwrite:
        return

    existing = [path for path in paths if path.exists()]
    if existing:
        names = ", ".join(path.name for path in existing)
        raise FileExistsError(f"Output file already exists: {names}. Use --force to overwrite.")


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Align audio to transcript using aeneas, output JSON/SRT/VTT.")
    parser.add_argument("audio", nargs="?", help="Optional single audio file to align")
    parser.add_argument("transcript", nargs="?", help="Optional transcript file for the single audio file")
    parser.add_argument("--input-dir", default="convert", help="Folder containing audio (.wav/.mp3) and .txt transcripts")
    parser.add_argument("--output-dir", default="aligned", help="Folder to write outputs (json/srt/vtt)")
    parser.add_argument("--language", default="eng", help="Aeneas language code, default: eng")
    parser.add_argument("--force", action="store_true", help="Overwrite existing output files")
    parser.add_argument(
        "--max-caption-chars",
        type=int,
        default=DEFAULT_MAX_CAPTION_CHARS,
        help=f"Maximum characters per generated subtitle cue, default: {DEFAULT_MAX_CAPTION_CHARS}",
    )
    parser.add_argument(
        "--max-caption-duration",
        type=float,
        default=DEFAULT_MAX_CAPTION_DURATION,
        help=f"Maximum seconds per generated subtitle cue, default: {DEFAULT_MAX_CAPTION_DURATION:g}",
    )
    parser.add_argument(
        "--no-pre-split",
        action="store_true",
        help="Align against the original transcript text instead of pre-splitting it into caption chunks",
    )
    return parser.parse_args(argv)


def align_file_pair(
    audio: Path,
    transcript: Path,
    output_dir: Path,
    language: str,
    tmp_dir: Path,
    overwrite: bool,
    max_caption_chars: int = DEFAULT_MAX_CAPTION_CHARS,
    max_caption_duration: float = DEFAULT_MAX_CAPTION_DURATION,
    pre_split_transcript: bool = True,
) -> None:
    if audio.suffix.lower() not in AUDIO_EXTENSIONS:
        raise ValueError(f"Unsupported audio file extension for {audio.name}; expected .wav or .mp3")

    final_json, srt_path, vtt_path = output_paths(output_dir, audio.stem)
    ensure_outputs_writable((final_json, srt_path, vtt_path), overwrite=overwrite)

    audio_for_aeneas = audio
    if audio.suffix.lower() != ".wav":
        audio_for_aeneas = convert_to_wav(audio, tmp_dir)

    transcript_for_aeneas = transcript
    if pre_split_transcript:
        transcript_for_aeneas, prepared_line_count = prepare_transcript_for_alignment(
            transcript,
            tmp_dir,
            max_chars=max_caption_chars,
        )
        print(f"      Prepared transcript: {prepared_line_count} caption-sized lines")

    json_path = run_alignment(audio_for_aeneas, transcript_for_aeneas, output_dir, language=language)

    if json_path.name != final_json.name:
        final_json.write_text(json_path.read_text(encoding="utf-8"), encoding="utf-8")
        json_path = final_json

    json_to_srt(json_path, srt_path, max_chars=max_caption_chars, max_duration=max_caption_duration)
    json_to_vtt(json_path, vtt_path, max_chars=max_caption_chars, max_duration=max_caption_duration)

    print(f"      Wrote: {json_path.name}, {srt_path.name}, {vtt_path.name}\n")


def selected_file_pair(args: argparse.Namespace) -> tuple[Path, Path] | None:
    if not args.audio and not args.transcript:
        return None
    if not args.audio or not args.transcript:
        raise ValueError("Provide both audio and transcript paths, or neither to process the input folder.")

    return Path(args.audio).expanduser().resolve(), Path(args.transcript).expanduser().resolve()


def cli(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        file_pair = selected_file_pair(args)
        output_dir = Path(args.output_dir).expanduser().resolve()

        with tempfile.TemporaryDirectory(prefix="aeneas_tmp_") as td:
            tmp_dir = Path(td)

            if file_pair:
                audio, transcript = file_pair
                print("Running aeneas alignment")
                print(f"      Audio:      {display_path(audio)}")
                print(f"      Transcript: {display_path(transcript)}")
                print(f"      Language:   {args.language}")
                print("      This can take a while for long audio files.")
                align_file_pair(
                    audio,
                    transcript,
                    output_dir,
                    args.language,
                    tmp_dir,
                    overwrite=args.force,
                    max_caption_chars=args.max_caption_chars,
                    max_caption_duration=args.max_caption_duration,
                    pre_split_transcript=not args.no_pre_split,
                )
                return 0

            input_dir = Path(args.input_dir).expanduser().resolve()
            if not input_dir.exists():
                print(f"ERROR: input dir not found: {input_dir}")
                return 1

            audio_files = find_audio_files(input_dir)
            if not audio_files:
                print(f"No .wav or .mp3 files found in {input_dir}")
                return 0

            failures = 0
            total = len(audio_files)
            for i, audio in enumerate(audio_files, start=1):
                transcript = input_dir / f"{audio.stem}.txt"

                if not transcript.exists():
                    print(f"[{i}/{total}] Skipping, transcript missing for {audio.name}: expected {transcript.name}")
                    print_batch_progress(i, total)
                    continue

                print(f"[{i}/{total}] Running aeneas alignment")
                print(f"      Audio:      {display_path(audio)}")
                print(f"      Transcript: {display_path(transcript)}")
                print(f"      Language:   {args.language}")
                print("      This can take a while for long audio files.")

                try:
                    align_file_pair(
                        audio,
                        transcript,
                        output_dir,
                        args.language,
                        tmp_dir,
                        overwrite=args.force,
                        max_caption_chars=args.max_caption_chars,
                        max_caption_duration=args.max_caption_duration,
                        pre_split_transcript=not args.no_pre_split,
                    )
                except FileExistsError as e:
                    print(f"      Skipping: {e}\n")
                except (FileNotFoundError, RuntimeError, ValueError, subprocess.SubprocessError) as e:
                    failures += 1
                    print(f"      ERROR: {e}\n", file=sys.stderr)
                print_batch_progress(i, total)

            if failures:
                print(f"Completed with {failures} failed file(s).", file=sys.stderr)
                return 1
    except (FileExistsError, FileNotFoundError, RuntimeError, ValueError, subprocess.SubprocessError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    return 0


def main() -> None:
    raise SystemExit(cli())


if __name__ == "__main__":
    main()
