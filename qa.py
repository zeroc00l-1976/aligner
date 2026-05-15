#!/usr/bin/env python3
import argparse
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from align import (
    AUDIO_EXTENSIONS,
    DEFAULT_MAX_CAPTION_CHARS,
    DEFAULT_MAX_CAPTION_DURATION,
    seconds_to_srt_time,
    seconds_to_vtt_time,
    transcript_caption_lines,
)


DEFAULT_MODEL = "base.en"
DEFAULT_DEVICE = "cpu"
DEFAULT_COMPUTE_TYPE = "int8"
DEFAULT_LANGUAGE = "en"
DEFAULT_THRESHOLD = 0.72
MIN_WORD_DURATION = 0.08
DEFAULT_RETIME_REPORT_SUFFIX = ".timing-report.json"
DEFAULT_INPUT_DIR = "convert"
DEFAULT_OUTPUT_DIR = "aligned"


def normalize_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s']", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_word(text: str) -> str:
    return normalize_text(text).replace(" ", "")


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
            "faster-whisper is required. Install project dependencies with: uv sync"
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


def transcribe_words(
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
        word_timestamps=True,
    )

    words: list[dict[str, float | str]] = []
    for segment in segments:
        for word in getattr(segment, "words", None) or []:
            text = (getattr(word, "word", "") or "").strip()
            normalized = normalize_word(text)
            if not text or not normalized:
                continue
            start = float(getattr(word, "start", segment.start))
            end = max(float(getattr(word, "end", segment.end)), start + MIN_WORD_DURATION)
            words.append(
                {
                    "start": start,
                    "end": end,
                    "text": text,
                    "normalized": normalized,
                }
            )
    return words


def official_word_tokens(transcript_path: Path) -> list[dict[str, float | str | None]]:
    raw_text = transcript_path.read_text(encoding="utf-8")
    tokens: list[dict[str, float | str | None]] = []
    for raw_word in re.findall(r"\S+", raw_text):
        normalized = normalize_word(raw_word)
        if not normalized:
            continue
        tokens.append(
            {
                "text": raw_word,
                "normalized": normalized,
                "start": None,
                "end": None,
                "timing_source": "unmatched",
            }
        )
    return tokens


def assign_block_proportionally(
    official_tokens: list[dict[str, float | str | None]],
    official_start: int,
    official_end: int,
    start_time: float,
    end_time: float,
    timing_source: str,
) -> None:
    count = official_end - official_start
    if count <= 0:
        return
    duration = max(end_time - start_time, count * MIN_WORD_DURATION)
    cursor = start_time
    for index in range(official_start, official_end):
        remaining = official_end - index
        word_duration = max(MIN_WORD_DURATION, (start_time + duration - cursor) / remaining)
        official_tokens[index]["start"] = cursor
        official_tokens[index]["end"] = cursor + word_duration
        official_tokens[index]["timing_source"] = timing_source
        cursor += word_duration


def transfer_word_timings(
    asr_words: list[dict[str, float | str]],
    official_tokens: list[dict[str, float | str | None]],
) -> list[dict[str, float | str | None]]:
    asr_normalized = [str(word["normalized"]) for word in asr_words]
    official_normalized = [str(token["normalized"]) for token in official_tokens]
    matcher = SequenceMatcher(None, asr_normalized, official_normalized, autojunk=False)

    for tag, asr_start, asr_end, official_start, official_end in matcher.get_opcodes():
        if tag == "equal":
            for offset, official_index in enumerate(range(official_start, official_end)):
                asr_word = asr_words[asr_start + offset]
                official_tokens[official_index]["start"] = float(asr_word["start"])
                official_tokens[official_index]["end"] = float(asr_word["end"])
                official_tokens[official_index]["timing_source"] = "matched"
        elif tag == "replace" and asr_end > asr_start and official_end > official_start:
            start_time = float(asr_words[asr_start]["start"])
            end_time = float(asr_words[asr_end - 1]["end"])
            assign_block_proportionally(
                official_tokens,
                official_start,
                official_end,
                start_time,
                end_time,
                "estimated_replace",
            )

    fill_missing_timings(official_tokens)
    return official_tokens


def fill_missing_timings(tokens: list[dict[str, float | str | None]]) -> None:
    index = 0
    while index < len(tokens):
        if tokens[index]["start"] is not None and tokens[index]["end"] is not None:
            index += 1
            continue

        block_start = index
        while index < len(tokens) and (tokens[index]["start"] is None or tokens[index]["end"] is None):
            index += 1
        block_end = index

        previous_timed = next((i for i in range(block_start - 1, -1, -1) if tokens[i]["end"] is not None), None)
        next_timed = next((i for i in range(block_end, len(tokens)) if tokens[i]["start"] is not None), None)

        if previous_timed is not None and next_timed is not None:
            start_time = float(tokens[previous_timed]["end"])
            end_time = max(float(tokens[next_timed]["start"]), start_time + ((block_end - block_start) * MIN_WORD_DURATION))
        elif previous_timed is not None:
            start_time = float(tokens[previous_timed]["end"])
            end_time = start_time + ((block_end - block_start) * 0.3)
        elif next_timed is not None:
            end_time = float(tokens[next_timed]["start"])
            start_time = max(0.0, end_time - ((block_end - block_start) * 0.3))
        else:
            start_time = 0.0
            end_time = (block_end - block_start) * 0.3

        assign_block_proportionally(tokens, block_start, block_end, start_time, end_time, "estimated_unmatched")


def build_captions_from_timed_words(
    timed_words: list[dict[str, float | str | None]],
    max_chars: int = DEFAULT_MAX_CAPTION_CHARS,
    max_duration: float = DEFAULT_MAX_CAPTION_DURATION,
) -> list[dict[str, float | str]]:
    captions: list[dict[str, float | str]] = []
    current_words: list[dict[str, float | str | None]] = []

    def flush() -> None:
        if not current_words:
            return
        text = " ".join(str(word["text"]) for word in current_words)
        captions.append(
            {
                "start": float(current_words[0]["start"]),
                "end": float(current_words[-1]["end"]),
                "text": text,
            }
        )
        current_words.clear()

    for word in timed_words:
        if word["start"] is None or word["end"] is None:
            continue
        candidate_words = [*current_words, word]
        candidate_text = " ".join(str(item["text"]) for item in candidate_words)
        candidate_duration = float(candidate_words[-1]["end"]) - float(candidate_words[0]["start"])
        sentence_break = re.search(r"[.!?][\"')\]]?$", str(word["text"])) is not None

        if current_words and (
            (max_chars > 0 and len(candidate_text) > max_chars)
            or (max_duration > 0 and candidate_duration > max_duration)
        ):
            flush()
        current_words.append(word)
        if sentence_break and len(" ".join(str(item["text"]) for item in current_words)) >= max_chars * 0.45:
            flush()

    flush()
    return captions


def write_official_srt(captions: list[dict[str, float | str]], output_path: Path) -> None:
    lines: list[str] = []
    for index, caption in enumerate(captions, start=1):
        lines.append(str(index))
        lines.append(f"{seconds_to_srt_time(float(caption['start']))} --> {seconds_to_srt_time(float(caption['end']))}")
        lines.append(str(caption["text"]))
        lines.append("")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def write_official_vtt(captions: list[dict[str, float | str]], output_path: Path) -> None:
    lines = ["WEBVTT", ""]
    for caption in captions:
        lines.append(f"{seconds_to_vtt_time(float(caption['start']))} --> {seconds_to_vtt_time(float(caption['end']))}")
        lines.append(str(caption["text"]))
        lines.append("")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def timing_report(
    timed_words: list[dict[str, float | str | None]],
    asr_words: list[dict[str, float | str]],
    captions: list[dict[str, float | str]],
) -> dict[str, Any]:
    source_counts: dict[str, int] = {}
    for word in timed_words:
        source = str(word.get("timing_source") or "unknown")
        source_counts[source] = source_counts.get(source, 0) + 1

    weak_spans: list[dict[str, Any]] = []
    index = 0
    while index < len(timed_words):
        source = str(timed_words[index].get("timing_source"))
        if source == "matched":
            index += 1
            continue
        start = index
        while index < len(timed_words) and str(timed_words[index].get("timing_source")) != "matched":
            index += 1
        end = index
        if end - start >= 3:
            weak_spans.append(
                {
                    "word_start": start + 1,
                    "word_count": end - start,
                    "start": timed_words[start]["start"],
                    "end": timed_words[end - 1]["end"],
                    "text": " ".join(str(word["text"]) for word in timed_words[start:end]),
                    "timing_source": str(timed_words[start].get("timing_source")),
                }
            )

    official_word_count = len(timed_words)
    matched_count = source_counts.get("matched", 0)
    return {
        "official_word_count": official_word_count,
        "asr_word_count": len(asr_words),
        "caption_count": len(captions),
        "matched_word_count": matched_count,
        "estimated_word_count": official_word_count - matched_count,
        "matched_ratio": round(matched_count / official_word_count, 3) if official_word_count else 0.0,
        "timing_source_counts": source_counts,
        "weak_span_count": len(weak_spans),
        "weak_spans": weak_spans,
    }


def retime_official_transcript(
    audio_path: Path,
    transcript_path: Path,
    model_name: str = DEFAULT_MODEL,
    device: str = DEFAULT_DEVICE,
    compute_type: str = DEFAULT_COMPUTE_TYPE,
    language: str | None = DEFAULT_LANGUAGE,
    beam_size: int = 1,
    max_caption_chars: int = DEFAULT_MAX_CAPTION_CHARS,
    max_caption_duration: float = DEFAULT_MAX_CAPTION_DURATION,
) -> tuple[list[dict[str, float | str]], dict[str, Any], list[dict[str, float | str]]]:
    asr_words = transcribe_words(
        audio_path,
        model_name=model_name,
        device=device,
        compute_type=compute_type,
        language=language,
        beam_size=beam_size,
    )
    if not asr_words:
        raise RuntimeError("ASR produced no word timestamps.")

    official_tokens = official_word_tokens(transcript_path)
    if not official_tokens:
        raise RuntimeError(f"Official transcript contains no usable words: {transcript_path}")

    timed_words = transfer_word_timings(asr_words, official_tokens)
    captions = build_captions_from_timed_words(
        timed_words,
        max_chars=max_caption_chars,
        max_duration=max_caption_duration,
    )
    report = timing_report(timed_words, asr_words, captions)
    return captions, report, asr_words


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


def parse_retime_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create audio-timed captions using official transcript text.")
    parser.add_argument("audio", nargs="?", help="Optional single audio/video file to transcribe for timing")
    parser.add_argument("transcript", nargs="?", help="Optional official hard transcript .txt file")
    parser.add_argument("--input-dir", default=DEFAULT_INPUT_DIR, help="Batch input folder, default: convert")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Batch output folder, default: aligned")
    parser.add_argument("--srt", help="Output official-text SRT path for single-file mode")
    parser.add_argument("--vtt", help="Optional output official-text WebVTT path")
    parser.add_argument("--report", help="Optional timing QA JSON report path")
    parser.add_argument("--asr-srt", help="Optional raw ASR timing SRT path")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"faster-whisper model, default: {DEFAULT_MODEL}")
    parser.add_argument("--device", default=DEFAULT_DEVICE, help=f"Transcription device, default: {DEFAULT_DEVICE}")
    parser.add_argument(
        "--compute-type",
        default=DEFAULT_COMPUTE_TYPE,
        help=f"faster-whisper compute type, default: {DEFAULT_COMPUTE_TYPE}",
    )
    parser.add_argument("--language", default=DEFAULT_LANGUAGE, help=f"Language code, default: {DEFAULT_LANGUAGE}")
    parser.add_argument("--beam-size", type=int, default=1, help="ASR beam size, default: 1")
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
    parser.add_argument("--show", type=int, default=8, help="Number of weak timing spans to print, default: 8")
    return parser.parse_args(argv)


def find_retime_audio_files(input_dir: Path) -> list[Path]:
    files = [path for path in input_dir.iterdir() if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS]
    files.sort()
    return files


def selected_retime_pair(args: argparse.Namespace) -> tuple[Path, Path] | None:
    if not args.audio and not args.transcript:
        return None
    if not args.audio or not args.transcript:
        raise ValueError("Provide both audio and transcript paths, or neither to process the input folder.")
    return Path(args.audio).expanduser().resolve(), Path(args.transcript).expanduser().resolve()


def retime_output_paths(
    audio_path: Path,
    output_dir: Path,
    srt_path: str | None = None,
    vtt_path: str | None = None,
    report_path: str | None = None,
) -> tuple[Path, Path, Path]:
    resolved_srt = Path(srt_path).expanduser().resolve() if srt_path else (output_dir / f"{audio_path.stem}.srt").resolve()
    resolved_vtt = Path(vtt_path).expanduser().resolve() if vtt_path else resolved_srt.with_suffix(".vtt")
    resolved_report = (
        Path(report_path).expanduser().resolve()
        if report_path
        else resolved_srt.with_suffix(DEFAULT_RETIME_REPORT_SUFFIX)
    )
    return resolved_srt, resolved_vtt, resolved_report


def retime_file_pair(
    audio_path: Path,
    transcript_path: Path,
    srt_path: Path,
    vtt_path: Path,
    report_path: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    captions, report, _asr_words = retime_official_transcript(
        audio_path,
        transcript_path,
        model_name=args.model,
        device=args.device,
        compute_type=args.compute_type,
        language=args.language,
        beam_size=args.beam_size,
        max_caption_chars=args.max_caption_chars,
        max_caption_duration=args.max_caption_duration,
    )

    write_official_srt(captions, srt_path)
    write_official_vtt(captions, vtt_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


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


def print_timing_report(report: dict[str, Any], limit: int) -> None:
    print(
        f"Retimed captions: {report['caption_count']} cue(s), "
        f"matched {report['matched_ratio']:.1%} of official words, "
        f"{report['weak_span_count']} weak span(s)"
    )
    for span in report["weak_spans"][:limit]:
        start = seconds_to_srt_time(float(span["start"])).replace(",", ".")
        end = seconds_to_srt_time(float(span["end"])).replace(",", ".")
        print()
        print(f"{start} --> {end}  {span['timing_source']} words={span['word_count']}")
        print(span["text"])


def retime_cli(argv: list[str] | None = None) -> int:
    try:
        args = parse_retime_args(argv)
        file_pair = selected_retime_pair(args)
        output_dir = Path(args.output_dir).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        if file_pair is None and (args.srt or args.vtt or args.report or args.asr_srt):
            raise ValueError("--srt, --vtt, --report, and --asr-srt are only supported with a single audio/transcript pair.")

        print(f"Retiming official transcript with faster-whisper model={args.model} device={args.device} compute={args.compute_type}")

        if file_pair:
            audio_path, transcript_path = file_pair
            if not audio_path.exists():
                raise FileNotFoundError(f"Audio/video not found: {audio_path}")
            if not transcript_path.exists():
                raise FileNotFoundError(f"Transcript not found: {transcript_path}")

            srt_path, vtt_path, report_path = retime_output_paths(
                audio_path,
                output_dir,
                srt_path=args.srt,
                vtt_path=args.vtt,
                report_path=args.report,
            )
            report = retime_file_pair(audio_path, transcript_path, srt_path, vtt_path, report_path, args)
            print(f"Wrote official SRT: {srt_path}")
            print(f"Wrote official VTT: {vtt_path}")
            print(f"Wrote timing QA report: {report_path}")

            if args.asr_srt:
                asr_words = transcribe_words(
                    audio_path,
                    model_name=args.model,
                    device=args.device,
                    compute_type=args.compute_type,
                    language=args.language,
                    beam_size=args.beam_size,
                )
                asr_segments = [{"start": word["start"], "end": word["end"], "text": word["text"]} for word in asr_words]
                asr_srt_path = Path(args.asr_srt).expanduser().resolve()
                write_asr_srt(asr_segments, asr_srt_path)
                print(f"Wrote ASR word timing SRT: {asr_srt_path}")

            print_timing_report(report, limit=args.show)
            return 0

        input_dir = Path(args.input_dir).expanduser().resolve()
        if not input_dir.exists():
            raise FileNotFoundError(f"Input dir not found: {input_dir}")

        audio_files = find_retime_audio_files(input_dir)
        if not audio_files:
            print(f"No .wav or .mp3 files found in {input_dir}")
            return 0

        failures = 0
        for index, audio_path in enumerate(audio_files, start=1):
            transcript_path = input_dir / f"{audio_path.stem}.txt"
            if not transcript_path.exists():
                print(f"[{index}/{len(audio_files)}] Skipping {audio_path.name}, missing {transcript_path.name}")
                continue

            srt_path, vtt_path, report_path = retime_output_paths(audio_path, output_dir)
            print(f"[{index}/{len(audio_files)}] Retiming {audio_path.name}")
            try:
                report = retime_file_pair(audio_path, transcript_path, srt_path, vtt_path, report_path, args)
            except (RuntimeError, ValueError, FileNotFoundError) as e:
                failures += 1
                print(f"      ERROR: {e}", file=sys.stderr)
                continue

            print(f"      Wrote: {srt_path.name}, {vtt_path.name}, {report_path.name}")
            print(
                f"      QA: matched {report['matched_ratio']:.1%}, "
                f"weak spans {report['weak_span_count']}"
            )

        if failures:
            print(f"Completed with {failures} failed file(s).", file=sys.stderr)
            return 1
    except (FileNotFoundError, RuntimeError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    return 0


def main() -> None:
    raise SystemExit(cli())


def retime_main() -> None:
    raise SystemExit(retime_cli())


if __name__ == "__main__":
    main()
