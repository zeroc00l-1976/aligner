#!/usr/bin/env python3
import argparse
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from align import (
    DEFAULT_MAX_CAPTION_CHARS,
    seconds_to_srt_time,
    transcript_caption_lines,
)


DEFAULT_MODEL = "base.en"
DEFAULT_DEVICE = "cpu"
DEFAULT_COMPUTE_TYPE = "int8"
DEFAULT_LANGUAGE = "en"
DEFAULT_THRESHOLD = 0.72


def normalize_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s']", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def similarity(left: str, right: str) -> float:
    left_normalized = normalize_text(left)
    right_normalized = normalize_text(right)
    if not left_normalized and not right_normalized:
        return 1.0
    if not left_normalized or not right_normalized:
        return 0.0
    return SequenceMatcher(None, left_normalized, right_normalized).ratio()


def load_faster_whisper_model(model_name: str, device: str, compute_type: str) -> Any:
    try:
        from faster_whisper import WhisperModel
    except ModuleNotFoundError as e:
        raise RuntimeError(
            "faster-whisper is required for transcript QA. Install/run with: uv run --group qa check-transcript ..."
        ) from e

    return WhisperModel(model_name, device=device, compute_type=compute_type)


def transcribe_audio(
    audio_path: Path,
    model_name: str = DEFAULT_MODEL,
    device: str = DEFAULT_DEVICE,
    compute_type: str = DEFAULT_COMPUTE_TYPE,
    language: str | None = DEFAULT_LANGUAGE,
    beam_size: int = 1,
) -> list[dict[str, float | str]]:
    model = load_faster_whisper_model(model_name, device=device, compute_type=compute_type)
    segments, _info = model.transcribe(
        str(audio_path),
        language=language or None,
        beam_size=beam_size,
        vad_filter=True,
    )

    results: list[dict[str, float | str]] = []
    for segment in segments:
        text = (segment.text or "").strip()
        if not text:
            continue
        results.append(
            {
                "start": float(segment.start),
                "end": float(segment.end),
                "text": text,
            }
        )
    return results


def best_official_window(
    asr_text: str,
    official_lines: list[str],
    start_index: int,
    max_window_lines: int = 5,
) -> tuple[int, int, str, float]:
    best_start = start_index
    best_count = 1
    best_text = official_lines[start_index] if start_index < len(official_lines) else ""
    best_score = similarity(asr_text, best_text)

    search_start = max(0, start_index - 2)
    search_end = min(len(official_lines), start_index + 6)
    for candidate_start in range(search_start, search_end):
        for count in range(1, max_window_lines + 1):
            candidate_end = candidate_start + count
            if candidate_end > len(official_lines):
                continue
            candidate_text = " ".join(official_lines[candidate_start:candidate_end])
            score = similarity(asr_text, candidate_text)
            if score > best_score:
                best_start = candidate_start
                best_count = count
                best_text = candidate_text
                best_score = score

    return best_start, best_count, best_text, best_score


def compare_asr_to_official(
    asr_segments: list[dict[str, float | str]],
    official_lines: list[str],
    threshold: float = DEFAULT_THRESHOLD,
) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    official_index = 0

    for segment in asr_segments:
        asr_text = str(segment["text"])
        if official_index >= len(official_lines):
            official_text = ""
            score = 0.0
            official_start = official_index
            official_count = 0
        else:
            official_start, official_count, official_text, score = best_official_window(
                asr_text,
                official_lines,
                official_index,
            )
            official_index = max(official_index, official_start + official_count)

        item = {
            "start": float(segment["start"]),
            "end": float(segment["end"]),
            "score": round(score, 3),
            "asr_text": asr_text,
            "official_text": official_text,
            "official_line_start": official_start + 1,
            "official_line_count": official_count,
        }
        matches.append(item)
        if score < threshold:
            mismatches.append(item)

    average_score = sum(item["score"] for item in matches) / len(matches) if matches else 0.0
    return {
        "threshold": threshold,
        "average_score": round(average_score, 3),
        "segment_count": len(matches),
        "mismatch_count": len(mismatches),
        "matches": matches,
        "mismatches": mismatches,
    }


def write_asr_srt(asr_segments: list[dict[str, float | str]], output_path: Path) -> None:
    lines: list[str] = []
    for index, segment in enumerate(asr_segments, start=1):
        lines.append(str(index))
        lines.append(f"{seconds_to_srt_time(float(segment['start']))} --> {seconds_to_srt_time(float(segment['end']))}")
        lines.append(str(segment["text"]))
        lines.append("")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def print_report(report: dict[str, Any], limit: int) -> None:
    print(
        f"ASR QA: {report['mismatch_count']} possible mismatch(es) / "
        f"{report['segment_count']} segment(s), average score {report['average_score']}"
    )
    for item in report["mismatches"][:limit]:
        start = seconds_to_srt_time(float(item["start"])).replace(",", ".")
        end = seconds_to_srt_time(float(item["end"])).replace(",", ".")
        print()
        print(f"{start} --> {end}  score={item['score']}")
        print(f"Official: {item['official_text']}")
        print(f"ASR:      {item['asr_text']}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare audio ASR against the official transcript for QA.")
    parser.add_argument("audio", help="Audio or video file to transcribe")
    parser.add_argument("transcript", help="Official hard transcript .txt file")
    parser.add_argument("--output", help="Optional JSON report path")
    parser.add_argument("--asr-srt", help="Optional SRT path for raw ASR timing review")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"faster-whisper model, default: {DEFAULT_MODEL}")
    parser.add_argument("--device", default=DEFAULT_DEVICE, help=f"Transcription device, default: {DEFAULT_DEVICE}")
    parser.add_argument(
        "--compute-type",
        default=DEFAULT_COMPUTE_TYPE,
        help=f"faster-whisper compute type, default: {DEFAULT_COMPUTE_TYPE}",
    )
    parser.add_argument("--language", default=DEFAULT_LANGUAGE, help=f"Language code, default: {DEFAULT_LANGUAGE}")
    parser.add_argument("--beam-size", type=int, default=1, help="ASR beam size, default: 1")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help="Mismatch threshold, default: 0.72")
    parser.add_argument(
        "--max-caption-chars",
        type=int,
        default=DEFAULT_MAX_CAPTION_CHARS,
        help=f"Official transcript chunk size, default: {DEFAULT_MAX_CAPTION_CHARS}",
    )
    parser.add_argument("--show", type=int, default=12, help="Number of mismatches to print, default: 12")
    return parser.parse_args(argv)


def cli(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        audio_path = Path(args.audio).expanduser().resolve()
        transcript_path = Path(args.transcript).expanduser().resolve()

        if not audio_path.exists():
            raise FileNotFoundError(f"Audio/video not found: {audio_path}")
        if not transcript_path.exists():
            raise FileNotFoundError(f"Transcript not found: {transcript_path}")

        print(f"Transcribing with faster-whisper model={args.model} device={args.device} compute={args.compute_type}")
        asr_segments = transcribe_audio(
            audio_path,
            model_name=args.model,
            device=args.device,
            compute_type=args.compute_type,
            language=args.language,
            beam_size=args.beam_size,
        )
        official_lines = transcript_caption_lines(transcript_path, max_chars=args.max_caption_chars)
        report = compare_asr_to_official(asr_segments, official_lines, threshold=args.threshold)

        if args.output:
            output_path = Path(args.output).expanduser().resolve()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(f"Wrote QA report: {output_path}")

        if args.asr_srt:
            asr_srt_path = Path(args.asr_srt).expanduser().resolve()
            write_asr_srt(asr_segments, asr_srt_path)
            print(f"Wrote ASR timing SRT: {asr_srt_path}")

        print_report(report, limit=args.show)
    except (FileNotFoundError, RuntimeError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    return 0


def main() -> None:
    raise SystemExit(cli())


if __name__ == "__main__":
    main()
