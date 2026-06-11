#!/usr/bin/env python3
"""Capture video frames from a reviewed visual manifest."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract frames for manifest segments marked as needing video.")
    parser.add_argument("manifest", help="Visual manifest JSON.")
    parser.add_argument("-o", "--output", required=True, help="Updated manifest output path.")
    parser.add_argument("--frames-dir", required=True, help="Directory where frame PNGs are written.")
    parser.add_argument("--video-source", default=None, help="Override video URL/local path. Defaults to manifest source.")
    parser.add_argument("--format", default="best[height<=720][ext=mp4]/18/best", help="yt-dlp format selector.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing frame files.")
    return parser.parse_args()


def is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def require_command(name: str, hint: str) -> None:
    if shutil.which(name) is None:
        raise SystemExit(f"Missing required command: {name}. {hint}")


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(command, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if process.returncode != 0:
        raise RuntimeError(
            f"Command failed ({process.returncode}): {' '.join(command)}\n"
            f"stdout:\n{process.stdout}\nstderr:\n{process.stderr}"
        )
    return process


def resolve_video_input(source: str, format_selector: str) -> str:
    if not is_url(source):
        path = Path(source).expanduser().resolve()
        if not path.exists():
            raise SystemExit(f"Local video source does not exist: {path}")
        return str(path)
    require_command("yt-dlp", "Install it with: pip install yt-dlp")
    process = run(["yt-dlp", "-f", format_selector, "-g", "--no-playlist", source])
    urls = [line.strip() for line in process.stdout.splitlines() if line.strip()]
    if not urls:
        raise RuntimeError("yt-dlp did not return a direct video URL.")
    return urls[-1]


def seconds_to_label(seconds: float) -> str:
    rounded = max(0, int(round(seconds)))
    hours, remainder = divmod(rounded, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}-{minutes:02d}-{secs:02d}"


def capture_frame(video_input: str, seconds: float, output_path: Path, overwrite: bool) -> None:
    require_command("ffmpeg", "Install it with: brew install ffmpeg")
    if output_path.exists() and not overwrite:
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg",
            "-y" if overwrite else "-n",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{seconds:.3f}",
            "-i",
            video_input,
            "-frames:v",
            "1",
            "-vf",
            "scale='min(1280,iw)':-2",
            str(output_path),
        ]
    )


def main() -> int:
    args = parse_args()
    manifest_path = Path(args.manifest).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    frames_dir = Path(args.frames_dir).expanduser().resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source = args.video_source or manifest.get("source")
    if not source:
        raise SystemExit("No video source found. Pass --video-source.")
    video_input = resolve_video_input(str(source), args.format)

    for segment in manifest.get("segments", []):
        if not segment.get("needs_video"):
            continue
        frames = []
        for index, seconds in enumerate(segment.get("sampling", {}).get("times", []), start=1):
            frame_path = frames_dir / segment["id"] / f"{segment['id']}_{index:02d}_{seconds_to_label(seconds)}.png"
            capture_frame(video_input, float(seconds), frame_path, args.overwrite)
            frames.append(
                {
                    "path": str(frame_path),
                    "seconds": float(seconds),
                    "timestamp": seconds_to_label(float(seconds)).replace("-", ":"),
                    "ocr": {"status": "pending"},
                    "vision": {"status": "not_run"},
                }
            )
        segment["frames"] = frames
        segment["frame_capture"] = {
            "status": "done",
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "source": source,
        }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
