#!/usr/bin/env python3
import argparse
import subprocess
import sys
from pathlib import Path

from align import require_ffmpeg


DEFAULT_STYLE = "FontName=Arial,FontSize=24,Outline=2,Shadow=1,Alignment=2,MarginV=36"


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


def require_subtitles_filter() -> None:
    require_ffmpeg()

    proc = subprocess.run(["ffmpeg", "-filters"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    filters = f"{proc.stdout}\n{proc.stderr}"
    if proc.returncode != 0 or " subtitles " not in filters:
        raise RuntimeError(
            "This ffmpeg build does not include the 'subtitles' filter required for open captions. "
            "Install an ffmpeg build with libass/subtitles support."
        )


def build_ffmpeg_command(
    video_path: Path,
    subtitle_path: Path,
    output_path: Path,
    overwrite: bool,
    style: str | None = DEFAULT_STYLE,
) -> list[str]:
    cmd = ["ffmpeg"]
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
    require_subtitles_filter()

    video_path = video_path.expanduser().resolve()
    subtitle_path = subtitle_path.expanduser().resolve()
    output_path = output_path.expanduser().resolve()

    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")
    if not subtitle_path.exists():
        raise FileNotFoundError(f"Subtitle file not found: {subtitle_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    ensure_output_writable(output_path, overwrite=overwrite)

    cmd = build_ffmpeg_command(video_path, subtitle_path, output_path, overwrite=overwrite, style=style)
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
