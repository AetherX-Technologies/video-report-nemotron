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
    parser.add_argument(
        "--download-dir",
        default=None,
        help="Directory for a local video copy when the source is a URL. Defaults to <frames-dir>/../source_video.",
    )
    parser.add_argument(
        "--no-download",
        action="store_true",
        help="Use a direct streaming URL instead of first downloading a stable local video copy.",
    )
    parser.add_argument(
        "--keep-video",
        action="store_true",
        help="Keep the downloaded local video copy. By default URL downloads are deleted after frame capture.",
    )
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


def resolve_video_input(
    source: str,
    format_selector: str,
    *,
    download_dir: Path | None,
    no_download: bool,
    overwrite: bool,
) -> tuple[str, str | None]:
    if not is_url(source):
        path = Path(source).expanduser().resolve()
        if not path.exists():
            raise SystemExit(f"Local video source does not exist: {path}")
        return str(path), str(path)
    require_command("yt-dlp", "Install it with: pip install yt-dlp")
    if not no_download:
        if download_dir is None:
            raise SystemExit("download_dir is required when downloading URL video sources")
        download_dir.mkdir(parents=True, exist_ok=True)
        output_template = download_dir / "source.%(ext)s"
        command = [
            "yt-dlp",
            "-f",
            format_selector,
            "--no-playlist",
            "-o",
            str(output_template),
        ]
        if overwrite:
            command.append("--force-overwrites")
        command.append(source)
        run(command)
        candidates = sorted(
            [path for path in download_dir.glob("source.*") if path.is_file()],
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            raise RuntimeError("yt-dlp completed but no local video file was found.")
        local_path = candidates[0].resolve()
        return str(local_path), str(local_path)

    process = run(["yt-dlp", "-f", format_selector, "-g", "--no-playlist", source])
    urls = [line.strip() for line in process.stdout.splitlines() if line.strip()]
    if not urls:
        raise RuntimeError("yt-dlp did not return a direct video URL.")
    return urls[-1], None


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
    download_dir = (
        Path(args.download_dir).expanduser().resolve()
        if args.download_dir
        else frames_dir.parent / "source_video"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source = args.video_source or manifest.get("source")
    if not source:
        raise SystemExit("No video source found. Pass --video-source.")
    video_input, local_video_path = resolve_video_input(
        str(source),
        args.format,
        download_dir=download_dir,
        no_download=args.no_download,
        overwrite=args.overwrite,
    )
    manifest["frame_capture_source"] = {
        "original_source": str(source),
        "video_input": video_input,
        "local_video_path": local_video_path,
        "downloaded_local_copy": bool(local_video_path and is_url(str(source))),
        "keep_video": args.keep_video,
    }

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
            "local_video_path": local_video_path,
        }

    downloaded_local_copy = bool(local_video_path and is_url(str(source)))
    if downloaded_local_copy and not args.keep_video:
        local_path = Path(local_video_path)
        try:
            local_path.unlink(missing_ok=True)
            manifest["frame_capture_source"]["cleanup"] = {
                "status": "deleted",
                "path": str(local_path),
                "deleted_at": datetime.now(timezone.utc).isoformat(),
            }
            for segment in manifest.get("segments", []):
                if segment.get("frame_capture"):
                    segment["frame_capture"]["local_video_path"] = None
                    segment["frame_capture"]["local_video_deleted"] = True
        except OSError as exc:
            manifest["frame_capture_source"]["cleanup"] = {
                "status": "failed",
                "path": str(local_path),
                "error": str(exc),
            }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
