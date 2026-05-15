from argparse import Namespace
from pathlib import Path

import pytest

from qa import (
    best_official_window,
    build_captions_from_timed_words,
    compare_asr_to_official,
    find_retime_audio_files,
    normalize_text,
    official_word_tokens,
    retime_cli,
    retime_output_paths,
    selected_retime_pair,
    similarity,
    timing_report,
    transfer_word_timings,
    write_asr_srt,
    write_official_srt,
    write_official_vtt,
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


def test_transfer_word_timings_maps_matching_official_words() -> None:
    asr_words = [
        {"start": 0.0, "end": 0.5, "text": "hello", "normalized": "hello"},
        {"start": 0.5, "end": 1.0, "text": "world", "normalized": "world"},
    ]
    official_tokens = [
        {"text": "Hello,", "normalized": "hello", "start": None, "end": None, "timing_source": "unmatched"},
        {"text": "world.", "normalized": "world", "start": None, "end": None, "timing_source": "unmatched"},
    ]

    timed = transfer_word_timings(asr_words, official_tokens)

    assert timed[0]["start"] == 0.0
    assert timed[0]["end"] == 0.5
    assert timed[0]["timing_source"] == "matched"
    assert timed[1]["start"] == 0.5
    assert timed[1]["end"] == 1.0
    assert timed[1]["timing_source"] == "matched"


def test_transfer_word_timings_interpolates_official_insertions() -> None:
    asr_words = [
        {"start": 0.0, "end": 0.5, "text": "hello", "normalized": "hello"},
        {"start": 1.0, "end": 1.5, "text": "world", "normalized": "world"},
    ]
    official_tokens = [
        {"text": "Hello", "normalized": "hello", "start": None, "end": None, "timing_source": "unmatched"},
        {"text": "there", "normalized": "there", "start": None, "end": None, "timing_source": "unmatched"},
        {"text": "world", "normalized": "world", "start": None, "end": None, "timing_source": "unmatched"},
    ]

    timed = transfer_word_timings(asr_words, official_tokens)

    assert timed[1]["timing_source"] == "estimated_unmatched"
    assert timed[1]["start"] == 0.5
    assert timed[1]["end"] == 1.0


def test_build_captions_from_timed_words_respects_character_limit() -> None:
    words = [
        {"text": "One", "start": 0.0, "end": 0.5},
        {"text": "two", "start": 0.5, "end": 1.0},
        {"text": "three", "start": 1.0, "end": 1.5},
        {"text": "four", "start": 1.5, "end": 2.0},
    ]

    captions = build_captions_from_timed_words(words, max_chars=13, max_duration=10)

    assert captions == [
        {"start": 0.0, "end": 1.5, "text": "One two three"},
        {"start": 1.5, "end": 2.0, "text": "four"},
    ]


def test_timing_report_counts_weak_spans() -> None:
    timed_words = [
        {"text": "one", "start": 0.0, "end": 0.5, "timing_source": "matched"},
        {"text": "two", "start": 0.5, "end": 1.0, "timing_source": "estimated_unmatched"},
        {"text": "three", "start": 1.0, "end": 1.5, "timing_source": "estimated_unmatched"},
        {"text": "four", "start": 1.5, "end": 2.0, "timing_source": "estimated_unmatched"},
    ]

    report = timing_report(timed_words, asr_words=[], captions=[])

    assert report["matched_word_count"] == 1
    assert report["estimated_word_count"] == 3
    assert report["weak_span_count"] == 1


def test_write_official_srt_and_vtt(tmp_path: Path) -> None:
    captions = [{"start": 0.0, "end": 1.5, "text": "Official text"}]
    srt_path = tmp_path / "official.srt"
    vtt_path = tmp_path / "official.vtt"

    write_official_srt(captions, srt_path)
    write_official_vtt(captions, vtt_path)

    assert "Official text" in srt_path.read_text(encoding="utf-8")
    assert vtt_path.read_text(encoding="utf-8").startswith("WEBVTT")


def test_official_word_tokens_preserve_original_text(tmp_path: Path) -> None:
    transcript = tmp_path / "transcript.txt"
    transcript.write_text("MS BRUCE: Hello, world.", encoding="utf-8")

    assert [token["text"] for token in official_word_tokens(transcript)] == ["MS", "BRUCE:", "Hello,", "world."]


def test_similarity_handles_empty_text() -> None:
    assert similarity("", "") == 1.0
    assert similarity("", "audio") == 0.0


def test_retime_output_paths_default_to_aligned_names(tmp_path: Path) -> None:
    srt_path, vtt_path, report_path = retime_output_paths(Path("convert/show.mp3"), tmp_path)

    assert srt_path == tmp_path / "show.srt"
    assert vtt_path == tmp_path / "show.vtt"
    assert report_path == tmp_path / "show.timing-report.json"


def test_selected_retime_pair_requires_both_positional_paths() -> None:
    assert selected_retime_pair(Namespace(audio=None, transcript=None)) is None

    with pytest.raises(ValueError):
        selected_retime_pair(Namespace(audio="audio.mp3", transcript=None))


def test_find_retime_audio_files_returns_supported_files_sorted(tmp_path: Path) -> None:
    (tmp_path / "b.mp3").write_text("", encoding="utf-8")
    (tmp_path / "a.wav").write_text("", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("", encoding="utf-8")

    assert [path.name for path in find_retime_audio_files(tmp_path)] == ["a.wav", "b.mp3"]


def test_retime_cli_batch_processes_same_stem_pairs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    input_dir = tmp_path / "convert"
    output_dir = tmp_path / "aligned"
    input_dir.mkdir()
    (input_dir / "briefing.mp3").write_text("", encoding="utf-8")
    (input_dir / "briefing.txt").write_text("Official transcript.", encoding="utf-8")

    def fake_retime_file_pair(audio_path, transcript_path, srt_path, vtt_path, report_path, args):
        srt_path.write_text("srt", encoding="utf-8")
        vtt_path.write_text("vtt", encoding="utf-8")
        report_path.write_text("{}", encoding="utf-8")
        return {"matched_ratio": 1.0, "weak_span_count": 0}

    monkeypatch.setattr("qa.retime_file_pair", fake_retime_file_pair)

    assert retime_cli(["--input-dir", str(input_dir), "--output-dir", str(output_dir)]) == 0
    assert (output_dir / "briefing.srt").exists()
    assert (output_dir / "briefing.vtt").exists()
    assert (output_dir / "briefing.timing-report.json").exists()
