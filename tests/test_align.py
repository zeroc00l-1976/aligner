import json
from pathlib import Path

import pytest

from align import (
    cli,
    caption_chunks_for_text,
    caption_cues_for_fragment,
    ensure_outputs_writable,
    ffmpeg_binary,
    find_audio_files,
    fragment_text,
    json_to_srt,
    json_to_vtt,
    output_paths,
    progress_bar,
    selected_file_pair,
    seconds_to_srt_time,
    seconds_to_vtt_time,
    split_text_for_captions,
)


def write_sync_map(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "fragments": [
                    {"begin": "0.000", "end": "1.234", "lines": ["Hello"]},
                    {"begin": "1.234", "end": "1.234", "lines": [""]},
                    {"begin": "1.234", "end": "2.500", "lines": ["world", "again"]},
                ]
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (-1, "00:00:00,000"),
        (0, "00:00:00,000"),
        (1.9996, "00:00:02,000"),
        (3661.2345, "01:01:01,235"),
    ],
)
def test_seconds_to_srt_time(seconds: float, expected: str) -> None:
    assert seconds_to_srt_time(seconds) == expected


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (-1, "00:00:00.000"),
        (0, "00:00:00.000"),
        (1.9996, "00:00:02.000"),
        (3661.2345, "01:01:01.235"),
    ],
)
def test_seconds_to_vtt_time(seconds: float, expected: str) -> None:
    assert seconds_to_vtt_time(seconds) == expected


def test_fragment_text_joins_non_empty_lines() -> None:
    assert fragment_text({"lines": [" first ", "", "second "]}) == "first second"


def test_split_text_for_captions_prefers_short_chunks() -> None:
    text = "This is a first sentence. This is a second sentence that should stay readable."

    assert split_text_for_captions(text, max_chars=32) == [
        "This is a first sentence.",
        "This is a second sentence that",
        "should stay readable.",
    ]


def test_caption_chunks_for_text_respects_duration_target() -> None:
    text = "one two three four five six seven eight nine ten"

    assert caption_chunks_for_text(text, duration=12, max_chars=100, max_duration=4) == [
        "one two three",
        "four five six seven",
        "eight nine ten",
    ]


def test_caption_cues_for_fragment_splits_and_distributes_time() -> None:
    cues = caption_cues_for_fragment(
        {
            "begin": "10.000",
            "end": "20.000",
            "lines": ["one two three four five six seven eight"],
        },
        max_chars=100,
        max_duration=5,
    )

    assert len(cues) == 2
    assert cues[0] == (10.0, 15.0, "one two three four")
    assert cues[1] == (15.0, 20.0, "five six seven eight")


def test_ffmpeg_binary_defaults_to_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALIGNER_FFMPEG", raising=False)

    assert ffmpeg_binary() == "ffmpeg"


def test_ffmpeg_binary_can_be_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALIGNER_FFMPEG", "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")

    assert ffmpeg_binary() == "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"


def test_progress_bar_renders_completion() -> None:
    assert progress_bar(5, 10, width=10) == "[#####.....]"
    assert progress_bar(10, 10, width=10) == "[##########]"


def test_json_to_srt_skips_empty_fragments(tmp_path: Path) -> None:
    json_path = tmp_path / "sample.json"
    srt_path = tmp_path / "sample.srt"
    write_sync_map(json_path)

    json_to_srt(json_path, srt_path, max_chars=100, max_duration=10)

    assert srt_path.read_text(encoding="utf-8") == (
        "1\n"
        "00:00:00,000 --> 00:00:01,234\n"
        "Hello\n"
        "\n"
        "2\n"
        "00:00:01,234 --> 00:00:02,500\n"
        "world again\n"
    )


def test_json_to_vtt_skips_empty_fragments(tmp_path: Path) -> None:
    json_path = tmp_path / "sample.json"
    vtt_path = tmp_path / "sample.vtt"
    write_sync_map(json_path)

    json_to_vtt(json_path, vtt_path, max_chars=100, max_duration=10)

    assert vtt_path.read_text(encoding="utf-8") == (
        "WEBVTT\n"
        "\n"
        "00:00:00.000 --> 00:00:01.234\n"
        "Hello\n"
        "\n"
        "00:00:01.234 --> 00:00:02.500\n"
        "world again\n"
    )


def test_find_audio_files_returns_supported_files_sorted(tmp_path: Path) -> None:
    for name in ["b.mp3", "notes.txt", "a.wav", "c.m4a"]:
        (tmp_path / name).write_text("", encoding="utf-8")

    assert [path.name for path in find_audio_files(tmp_path)] == ["a.wav", "b.mp3"]


def test_output_paths_uses_expected_extensions(tmp_path: Path) -> None:
    assert [path.name for path in output_paths(tmp_path, "interview")] == [
        "interview.json",
        "interview.srt",
        "interview.vtt",
    ]


def test_ensure_outputs_writable_rejects_existing_files_without_force(tmp_path: Path) -> None:
    existing = tmp_path / "interview.srt"
    existing.write_text("", encoding="utf-8")

    with pytest.raises(FileExistsError, match="Use --force"):
        ensure_outputs_writable((existing,), overwrite=False)


def test_ensure_outputs_writable_allows_existing_files_with_force(tmp_path: Path) -> None:
    existing = tmp_path / "interview.srt"
    existing.write_text("", encoding="utf-8")

    ensure_outputs_writable((existing,), overwrite=True)


def test_selected_file_pair_defaults_to_batch_mode() -> None:
    assert selected_file_pair(cli_args()) is None


def test_selected_file_pair_requires_both_paths() -> None:
    with pytest.raises(ValueError, match="both audio and transcript"):
        selected_file_pair(cli_args(audio="audio.wav"))


def test_cli_defaults_to_batch_mode_when_no_file_pair_is_given(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    empty_input_dir = tmp_path / "input"
    empty_input_dir.mkdir()

    exit_code = cli(["--input-dir", str(empty_input_dir)])

    assert exit_code == 0
    assert "No .wav or .mp3 files found" in capsys.readouterr().out


def test_cli_skips_existing_outputs_in_batch_mode(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    output_dir.mkdir()
    (input_dir / "interview.wav").write_text("", encoding="utf-8")
    (input_dir / "interview.txt").write_text("hello", encoding="utf-8")
    (output_dir / "interview.srt").write_text("", encoding="utf-8")

    exit_code = cli(["--input-dir", str(input_dir), "--output-dir", str(output_dir)])

    assert exit_code == 0
    assert "Skipping: Output file already exists" in capsys.readouterr().out


def test_cli_rejects_incomplete_file_pair(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli(["audio.wav"])

    assert exit_code == 1
    assert "Provide both audio and transcript paths" in capsys.readouterr().err


def cli_args(**kwargs: str | bool | None) -> object:
    class Args:
        audio = None
        transcript = None

    args = Args()
    for key, value in kwargs.items():
        setattr(args, key, value)
    return args
