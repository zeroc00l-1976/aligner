#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from align import FFMPEG_ENV_VAR, ffmpeg_binary


DEFAULT_STYLE = "FontName=Arial,FontSize=24,Outline=2,Shadow=1,Alignment=2,MarginV=36"
DEFAULT_VIDEO_CODEC = "libx264"
DEFAULT_PROFILE = "quick"
QUALITY_PROFILES = {
    "quick": {"crf": 30, "preset": "ultrafast", "height": 720},
    "medium": {"crf": 23, "preset": "medium", "height": None},
    "high": {"crf": 18, "preset": "slow", "height": None},
}
HOMEBREW_FFMPEG_FULL_PATHS = (
    "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
    "/usr/local/opt/ffmpeg-full/bin/ffmpeg",
)
PROGRESS_BAR_WIDTH = 30


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


def profile_settings(profile: str) -> dict[str, int | str | None]:
    try:
        return QUALITY_PROFILES[profile].copy()
    except KeyError as e:
        profiles = ", ".join(QUALITY_PROFILES)
        raise ValueError(f"Unknown quality profile '{profile}'. Choose one of: {profiles}") from e


def build_video_filter(subtitle_path: Path, style: str | None = DEFAULT_STYLE, height: int | None = None) -> str:
    filters = []
    if height:
        filters.append(f"scale=-2:{height}")
    filters.append(subtitles_filter(subtitle_path, style=style))
    return ",".join(filters)


def ensure_output_writable(output_path: Path, overwrite: bool) -> None:
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"Output video already exists: {output_path}. Use --force to overwrite.")


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds + 0.5))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:d}:{secs:02d}"


def progress_bar(current: float, total: float, width: int = PROGRESS_BAR_WIDTH) -> str:
    if total <= 0:
        return f"[{'?' * width}]"
    ratio = min(1.0, max(0.0, current / total))
    filled = int(width * ratio)
    return f"[{'#' * filled}{'.' * (width - filled)}]"


def parse_ffmpeg_time(value: str) -> float | None:
    parts = value.split(":")
    if len(parts) != 3:
        return None
    try:
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = float(parts[2])
    except ValueError:
        return None
    return (hours * 3600) + (minutes * 60) + seconds


def parse_rate(value: str | None) -> float | None:
    if not value or value == "0/0":
        return None
    if "/" not in value:
        try:
            return float(value)
        except ValueError:
            return None
    numerator, denominator = value.split("/", 1)
    try:
        denominator_float = float(denominator)
        if denominator_float == 0:
            return None
        return float(numerator) / denominator_float
    except ValueError:
        return None


def format_rate(rate: float | None) -> str:
    if rate is None:
        return "unknown fps"
    if rate.is_integer():
        return f"{int(rate)} fps"
    return f"{rate:.2f} fps"


def format_bitrate(bit_rate: str | None) -> str:
    if not bit_rate:
        return "unknown bitrate"
    try:
        bits = int(bit_rate)
    except ValueError:
        return "unknown bitrate"
    if bits >= 1_000_000:
        return f"{bits / 1_000_000:.1f} Mbps"
    if bits >= 1_000:
        return f"{bits / 1_000:.0f} kbps"
    return f"{bits} bps"


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


def ffprobe_binary_for(ffmpeg_path: str) -> str:
    candidate = Path(ffmpeg_path).with_name("ffprobe")
    if candidate.exists():
        return str(candidate)
    return "ffprobe"


def probe_video_info(video_path: Path, ffmpeg_path: str) -> dict[str, str | int | float | None]:
    ffprobe = ffprobe_binary_for(ffmpeg_path)
    proc = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height,avg_frame_rate,bit_rate,pix_fmt",
            "-show_entries",
            "format=duration,bit_rate",
            "-of",
            "json",
            str(video_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0:
        return {}

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}

    streams = data.get("streams") or []
    stream = streams[0] if streams else {}
    fmt = data.get("format") or {}
    return {
        "codec": stream.get("codec_name"),
        "width": stream.get("width"),
        "height": stream.get("height"),
        "fps": parse_rate(stream.get("avg_frame_rate")),
        "pix_fmt": stream.get("pix_fmt"),
        "bitrate": stream.get("bit_rate") or fmt.get("bit_rate"),
        "duration": fmt.get("duration"),
    }


def describe_video_info(info: dict[str, str | int | float | None]) -> str:
    width = info.get("width")
    height = info.get("height")
    size = f"{width}x{height}" if width and height else "unknown size"
    codec = info.get("codec") or "unknown codec"
    pix_fmt = info.get("pix_fmt") or "unknown pixel format"
    return f"{size}, {format_rate(info.get('fps'))}, {codec}, {pix_fmt}, {format_bitrate(info.get('bitrate'))}"


def probe_duration(video_path: Path, ffmpeg_path: str) -> float | None:
    ffprobe = ffprobe_binary_for(ffmpeg_path)
    proc = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0:
        return None
    try:
        duration = float(proc.stdout.strip())
    except ValueError:
        return None
    return duration if duration > 0 else None


def build_ffmpeg_command(
    video_path: Path,
    subtitle_path: Path,
    output_path: Path,
    overwrite: bool,
    style: str | None = DEFAULT_STYLE,
    ffmpeg_path: str | None = None,
    video_codec: str = DEFAULT_VIDEO_CODEC,
    preset: str = "medium",
    crf: int = 23,
    height: int | None = None,
) -> list[str]:
    cmd = [ffmpeg_path or ffmpeg_binary()]
    cmd.append("-y" if overwrite else "-n")
    cmd.extend(
        [
            "-hide_banner",
            "-v",
            "error",
            "-nostats",
            "-progress",
            "pipe:1",
            "-i",
            str(video_path),
            "-vf",
            build_video_filter(subtitle_path, style=style, height=height),
            "-c:v",
            video_codec,
            "-preset",
            preset,
            "-crf",
            str(crf),
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
    video_codec: str = DEFAULT_VIDEO_CODEC,
    preset: str = "medium",
    crf: int = 23,
    height: int | None = None,
    profile: str = DEFAULT_PROFILE,
    show_progress: bool = True,
) -> None:
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
    duration = probe_duration(video_path, ffmpeg_path)
    video_info = probe_video_info(video_path, ffmpeg_path)

    cmd = build_ffmpeg_command(
        video_path,
        subtitle_path,
        output_path,
        overwrite=overwrite,
        style=style,
        ffmpeg_path=ffmpeg_path,
        video_codec=video_codec,
        preset=preset,
        crf=crf,
        height=height,
    )
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    current_time = 0.0

    if show_progress:
        if video_info:
            print(f"Source video: {describe_video_info(video_info)}")
            if height:
                print(f"Output uses the {profile} profile and limits height to {height}p for faster review.")
            else:
                print("Output keeps source resolution and frame rate unless ffmpeg requires a compatibility conversion.")
        print(f"Burning captions with {Path(ffmpeg_path).name} ({profile}: {video_codec}, CRF {crf}, preset {preset})")

    assert proc.stdout is not None
    for raw_line in proc.stdout:
        line = raw_line.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key == "out_time":
            parsed_time = parse_ffmpeg_time(value)
            if parsed_time is not None:
                current_time = parsed_time
        elif key in {"out_time_us", "out_time_ms"}:
            try:
                current_time = int(value) / 1_000_000
            except ValueError:
                pass
        elif key == "progress" and show_progress:
            if value == "end" and duration:
                current_time = duration
            if duration:
                percent = min(100.0, max(0.0, (current_time / duration) * 100))
                print(
                    f"\r{progress_bar(current_time, duration)} {percent:5.1f}% "
                    f"{format_duration(current_time)} / {format_duration(duration)}",
                    end="",
                    flush=True,
                )
            else:
                print(f"\rProcessed {format_duration(current_time)}", end="", flush=True)

    stderr = proc.stderr.read() if proc.stderr else ""
    return_code = proc.wait()
    if show_progress:
        print()
    if return_code != 0:
        raise RuntimeError(f"ffmpeg failed burning subtitles into {output_path.name}:\n{stderr}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Burn SRT subtitles into a video as open captions.")
    parser.add_argument("video", help="Input video file")
    parser.add_argument("subtitles", help="Input .srt subtitle file")
    parser.add_argument("output", help="Output video file")
    parser.add_argument("--force", action="store_true", help="Overwrite the output video if it already exists")
    parser.add_argument(
        "--quality",
        choices=tuple(QUALITY_PROFILES),
        default=DEFAULT_PROFILE,
        help="Named output profile: quick for caption review, medium for normal export, high for final quality",
    )
    parser.add_argument("--crf", type=int, help="Override profile CRF; lower is larger/better")
    parser.add_argument("--preset", help="Override profile x264 speed/compression preset")
    parser.add_argument("--height", type=int, help="Override profile output height; preserves aspect ratio")
    parser.add_argument("--video-codec", default=DEFAULT_VIDEO_CODEC, help="Video codec to use, default: libx264")
    parser.add_argument("--no-progress", action="store_true", help="Hide ffmpeg progress output")
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
        settings = profile_settings(args.quality)
        crf = args.crf if args.crf is not None else settings["crf"]
        preset = args.preset if args.preset is not None else settings["preset"]
        height = args.height if args.height is not None else settings["height"]
        style = None if args.no_style else args.style
        burn_subtitles(
            Path(args.video),
            Path(args.subtitles),
            Path(args.output),
            overwrite=args.force,
            style=style,
            video_codec=args.video_codec,
            preset=str(preset),
            crf=int(crf),
            height=int(height) if height is not None else None,
            profile=args.quality,
            show_progress=not args.no_progress,
        )
    except (FileExistsError, FileNotFoundError, RuntimeError, ValueError, subprocess.SubprocessError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print(f"Wrote open-captioned video: {args.output}")
    return 0


def main() -> None:
    raise SystemExit(cli())


if __name__ == "__main__":
    main()
