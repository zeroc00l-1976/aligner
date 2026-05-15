#!/usr/bin/env python3
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from align import FFMPEG_ENV_VAR, ffmpeg_binary, require_ffmpeg


DEFAULT_STYLE = "FontName=Arial,FontSize=24,Outline=2,Shadow=1,Alignment=2,MarginV=36"
HOMEBREW_FFMPEG_FULL_PATHS = (
    "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
    "/usr/local/opt/ffmpeg-full/bin/ffmpeg",
)


def escape_filter_value(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace(":", "\\:")
        .replace(",", "\\,")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )


def subtitles_filter(subtitle_path: Path, style: str | None = DEFAULT_STYLE) -> str:
    escaped_path = escape_filter_value(str(subtitle_path.resolve()))
    filter_value = f"subtitles=filename='{escaped_path}'"
    if style:
        filter_value += f":force_style='{escape_filter_value(style)}'"
    return filter_value


def ensure_output_writable(output_path: Path, overwrite: bool) -> None:
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"Output video already exists: {output_path}. Use --force to overwrite.")


def ffmpeg_has_subtitles_filter(binary: str) -> bool:
    proc = subprocess.run([binary, "-filters"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    filters = f"{proc.stdout}\n{proc.stderr}"
    return proc.returncode == 0 and " subtitles " in filters


def subtitle_capable_ffmpeg_binary() -> str:
    env_binary = os.environ.get(FFMPEG_ENV_VAR)
    if env_binary:
        if ffmpeg_has_subtitles_filter(env_binary):
            return env_binary
        raise RuntimeError(f"{FFMPEG_ENV_VAR} points to ffmpeg without the required 'subtitles' filter: {env_binary}")

    default_binary = ffmpeg_binary()
    if shutil.which(default_binary) and ffmpeg_has_subtitles_filter(default_binary):
        return default_binary

    for candidate in HOMEBREW_FFMPEG_FULL_PATHS:
        if Path(candidate).exists() and ffmpeg_has_subtitles_filter(candidate):
            return candidate

    raise RuntimeError(
        "No ffmpeg build with the 'subtitles' filter was found. Install an ffmpeg build with "
        "libass/subtitles support. On macOS with Homebrew, run: brew install ffmpeg-full"
    )


def require_subtitles_filter() -> None:
    subtitle_capable_ffmpeg_binary()


def build_ffmpeg_command(
    video_path: Path,
    subtitle_path: Path,
    output_path: Path,
    overwrite: bool,
    style: str | None = DEFAULT_STYLE,
    ffmpeg_path: str | None = None,
) -> list[str]:
    cmd = [ffmpeg_path or ffmpeg_binary()]
    cmd.append("-y" if overwrite else "-n")
    cmd.extend(
        [
            "-i",
            str(video_path),
            "-vf",
            subtitles_filter(subtitle_path, style=style),
            "-c:a",
            "copy",
            str(output_path),
        ]
    )
    return cmd


def burn_subtitles(
    video_path: Path,
    subtitle_path: Path,
    output_path: Path,
    overwrite: bool = False,
    style: str | None = DEFAULT_STYLE,
) -> None:
    require_ffmpeg()
    ffmpeg_path = subtitle_capable_ffmpeg_binary()

    video_path = video_path.expanduser().resolve()
    subtitle_path = subtitle_path.expanduser().resolve()
    output_path = output_path.expanduser().resolve()

    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")
    if not subtitle_path.exists():
        raise FileNotFoundError(f"Subtitle file not found: {subtitle_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    ensure_output_writable(output_path, overwrite=overwrite)

    cmd = build_ffmpeg_command(
        video_path,
        subtitle_path,
        output_path,
        overwrite=overwrite,
        style=style,
        ffmpeg_path=ffmpeg_path,
    )
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed burning subtitles into {output_path.name}:\n{proc.stderr}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Burn SRT subtitles into a video as open captions.")
    parser.add_argument("video", help="Input video file")
    parser.add_argument("subtitles", help="Input .srt subtitle file")
    parser.add_argument("output", help="Output video file")
    parser.add_argument("--force", action="store_true", help="Overwrite the output video if it already exists")
    parser.add_argument(
        "--style",
        default=DEFAULT_STYLE,
        help="Optional ffmpeg force_style string for subtitle appearance",
    )
    parser.add_argument(
        "--no-style",
        action="store_true",
        help="Use ffmpeg's default subtitle styling instead of the built-in style",
    )
    return parser.parse_args(argv)


def cli(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        style = None if args.no_style else args.style
        burn_subtitles(
            Path(args.video),
            Path(args.subtitles),
            Path(args.output),
            overwrite=args.force,
            style=style,
        )
    except (FileExistsError, FileNotFoundError, RuntimeError, subprocess.SubprocessError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print(f"Wrote open-captioned video: {args.output}")
    return 0


def main() -> None:
    raise SystemExit(cli())


if __name__ == "__main__":
    main()
