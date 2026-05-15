from pathlib import Path

import pytest

from burn import (
    DEFAULT_STYLE,
    build_ffmpeg_command,
    ensure_output_writable,
    escape_filter_value,
    ffmpeg_has_subtitles_filter,
    require_subtitles_filter,
    subtitles_filter,
    subtitle_capable_ffmpeg_binary,
)


def test_escape_filter_value_escapes_ffmpeg_filter_special_chars() -> None:
    assert escape_filter_value(r"/tmp/a:b,c[1]\clip's.srt") == r"/tmp/a\:b\,c\[1\]\\clip\'s.srt"


def test_subtitles_filter_includes_default_style(tmp_path: Path) -> None:
    subtitle_path = tmp_path / "captions.srt"
    subtitle_path.write_text("", encoding="utf-8")
    escaped_style = DEFAULT_STYLE.replace(",", "\\,")

    filter_value = subtitles_filter(subtitle_path)

    assert filter_value.startswith("subtitles=filename='")
    assert str(subtitle_path.resolve()) in filter_value
    assert f"force_style='{escaped_style}'" in filter_value


def test_subtitles_filter_can_disable_style(tmp_path: Path) -> None:
    subtitle_path = tmp_path / "captions.srt"
    subtitle_path.write_text("", encoding="utf-8")

    assert "force_style" not in subtitles_filter(subtitle_path, style=None)


def test_ensure_output_writable_rejects_existing_output_without_force(tmp_path: Path) -> None:
    output_path = tmp_path / "output.mp4"
    output_path.write_text("", encoding="utf-8")

    with pytest.raises(FileExistsError, match="Use --force"):
        ensure_output_writable(output_path, overwrite=False)


def test_ensure_output_writable_allows_existing_output_with_force(tmp_path: Path) -> None:
    output_path = tmp_path / "output.mp4"
    output_path.write_text("", encoding="utf-8")

    ensure_output_writable(output_path, overwrite=True)


def test_build_ffmpeg_command_uses_subtitles_filter_and_copies_audio(tmp_path: Path) -> None:
    video_path = tmp_path / "input.mp4"
    subtitle_path = tmp_path / "captions.srt"
    output_path = tmp_path / "output.mp4"

    cmd = build_ffmpeg_command(video_path, subtitle_path, output_path, overwrite=False, style=None)

    assert cmd[:4] == ["ffmpeg", "-n", "-i", str(video_path)]
    assert "-vf" in cmd
    assert f"subtitles=filename='{subtitle_path.resolve()}'" in cmd
    assert cmd[-3:] == ["-c:a", "copy", str(output_path)]


def test_build_ffmpeg_command_uses_configured_ffmpeg_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALIGNER_FFMPEG", "/custom/ffmpeg")

    cmd = build_ffmpeg_command(
        tmp_path / "input.mp4",
        tmp_path / "captions.srt",
        tmp_path / "output.mp4",
        overwrite=False,
        style=None,
    )

    assert cmd[0] == "/custom/ffmpeg"


def test_ffmpeg_has_subtitles_filter_detects_available_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    class Proc:
        returncode = 0
        stdout = "Filters:\n .. subtitles         V->V Render text subtitles\n"
        stderr = ""

    monkeypatch.setattr("burn.subprocess.run", lambda *args, **kwargs: Proc())

    assert ffmpeg_has_subtitles_filter("/custom/ffmpeg") is True


def test_ffmpeg_has_subtitles_filter_rejects_missing_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    class Proc:
        returncode = 0
        stdout = "Filters:\n .. drawtext\n"
        stderr = ""

    monkeypatch.setattr("burn.subprocess.run", lambda *args, **kwargs: Proc())

    assert ffmpeg_has_subtitles_filter("/custom/ffmpeg") is False


def test_subtitle_capable_ffmpeg_prefers_env_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALIGNER_FFMPEG", "/custom/ffmpeg")
    monkeypatch.setattr("burn.ffmpeg_has_subtitles_filter", lambda binary: binary == "/custom/ffmpeg")

    assert subtitle_capable_ffmpeg_binary() == "/custom/ffmpeg"


def test_subtitle_capable_ffmpeg_rejects_bad_env_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALIGNER_FFMPEG", "/custom/ffmpeg")
    monkeypatch.setattr("burn.ffmpeg_has_subtitles_filter", lambda binary: False)

    with pytest.raises(RuntimeError, match="ALIGNER_FFMPEG"):
        subtitle_capable_ffmpeg_binary()


def test_subtitle_capable_ffmpeg_uses_homebrew_full_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALIGNER_FFMPEG", raising=False)
    monkeypatch.setattr("burn.shutil.which", lambda binary: "/usr/bin/ffmpeg")
    monkeypatch.setattr("burn.ffmpeg_has_subtitles_filter", lambda binary: binary.endswith("ffmpeg-full/bin/ffmpeg"))
    monkeypatch.setattr("burn.HOMEBREW_FFMPEG_FULL_PATHS", ("/fake/ffmpeg-full/bin/ffmpeg",))
    monkeypatch.setattr("burn.Path.exists", lambda self: str(self) == "/fake/ffmpeg-full/bin/ffmpeg")

    assert subtitle_capable_ffmpeg_binary() == "/fake/ffmpeg-full/bin/ffmpeg"


def test_require_subtitles_filter_rejects_ffmpeg_without_subtitles_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("burn.subtitle_capable_ffmpeg_binary", lambda: (_ for _ in ()).throw(RuntimeError("subtitles")))

    with pytest.raises(RuntimeError, match="subtitles"):
        require_subtitles_filter()
