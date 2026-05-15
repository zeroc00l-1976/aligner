from pathlib import Path

import pytest

from qa import (
    best_official_window,
    compare_asr_to_official,
    normalize_text,
    similarity,
    write_asr_srt,
)


def test_normalize_text_removes_noise() -> None:
    assert normalize_text("Hello, WORLD!  It’s OK.") == "hello world it s ok"


def test_similarity_scores_close_text_higher_than_different_text() -> None:
    assert similarity("The United States remains committed", "United States remains committed") > 0.8
    assert similarity("The United States remains committed", "Completely unrelated words") < 0.5


def test_best_official_window_finds_nearby_match() -> None:
    official = [
        "Welcome everyone",
        "The United States remains committed to diplomacy",
        "We will take your questions",
    ]

    start, count, text, score = best_official_window("United States is committed to diplomacy", official, 0)

    assert start == 1
    assert count == 1
    assert text == "The United States remains committed to diplomacy"
    assert score > 0.7


def test_compare_asr_to_official_reports_mismatches() -> None:
    asr_segments = [
        {"start": 0.0, "end": 2.0, "text": "Welcome everyone"},
        {"start": 2.0, "end": 4.0, "text": "this text is not in the transcript"},
    ]
    official = ["Welcome everyone", "The United States remains committed"]

    report = compare_asr_to_official(asr_segments, official, threshold=0.72)

    assert report["segment_count"] == 2
    assert report["mismatch_count"] == 1
    assert report["mismatches"][0]["asr_text"] == "this text is not in the transcript"


def test_write_asr_srt(tmp_path: Path) -> None:
    output_path = tmp_path / "asr.srt"

    write_asr_srt(
        [
            {"start": 0.0, "end": 1.5, "text": "Hello"},
            {"start": 1.5, "end": 3.0, "text": "world"},
        ],
        output_path,
    )

    assert output_path.read_text(encoding="utf-8") == (
        "1\n"
        "00:00:00,000 --> 00:00:01,500\n"
        "Hello\n"
        "\n"
        "2\n"
        "00:00:01,500 --> 00:00:03,000\n"
        "world\n"
    )


def test_similarity_handles_empty_text() -> None:
    assert similarity("", "") == 1.0
    assert similarity("", "audio") == 0.0
