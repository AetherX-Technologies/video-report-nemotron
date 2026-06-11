#!/usr/bin/env python3
"""Run LiteParse OCR over captured frames and update the manifest."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OCR captured video frames with LiteParse.")
    parser.add_argument("manifest", help="Manifest with captured frame paths.")
    parser.add_argument("-o", "--output", required=True, help="Updated manifest output path.")
    parser.add_argument("--ocr-dir", required=True, help="Directory for OCR JSON outputs.")
    parser.add_argument("--liteparse-bin", default=None, help="liteparse/lit binary path.")
    parser.add_argument("--ocr-language", default="chi_sim+eng", help="Tesseract language code.")
    parser.add_argument("--min-text-chars", type=int, default=20, help="Minimum OCR text length for adequate quality.")
    parser.add_argument(
        "--allow-missing-deps",
        action="store_true",
        help="Write dependency-missing statuses instead of failing.",
    )
    return parser.parse_args()


def find_liteparse(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    return shutil.which("liteparse") or shutil.which("lit")


def has_imagemagick() -> bool:
    return shutil.which("magick") is not None or shutil.which("convert") is not None


def find_tessdata() -> str | None:
    candidates = [
        os.environ.get("TESSDATA_PREFIX"),
        "/opt/homebrew/share/tessdata",
        "/usr/local/share/tessdata",
    ]
    for candidate in candidates:
        if candidate and (Path(candidate) / "eng.traineddata").exists():
            return candidate
    for binary in ("tesseract",):
        executable = shutil.which(binary)
        if not executable:
            continue
        try:
            process = subprocess.run(
                [executable, "--list-langs"],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError:
            continue
        for line in process.stderr.splitlines() + process.stdout.splitlines():
            line = line.strip()
            if line.endswith("tessdata/") or line.endswith("tessdata"):
                path = Path(line.rstrip("/"))
                if (path / "eng.traineddata").exists():
                    return str(path)
    return None


def run(command: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        command,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    if process.returncode != 0:
        raise RuntimeError(
            f"Command failed ({process.returncode}): {' '.join(command)}\n"
            f"stdout:\n{process.stdout}\nstderr:\n{process.stderr}"
        )
    return process


def collect_text(value: Any) -> list[str]:
    texts: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() == "text" and isinstance(item, str):
                texts.append(item)
            else:
                texts.extend(collect_text(item))
    elif isinstance(value, list):
        for item in value:
            texts.extend(collect_text(item))
    return texts


def load_ocr_text(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore")
    text = "\n".join(text.strip() for text in collect_text(data) if text.strip())
    if not text:
        text = json.dumps(data, ensure_ascii=False)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def ocr_frame(
    liteparse_bin: str,
    frame_path: Path,
    ocr_path: Path,
    language: str,
    tessdata_prefix: str | None,
) -> str:
    ocr_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    if tessdata_prefix:
        env["TESSDATA_PREFIX"] = tessdata_prefix
    run(
        [
            liteparse_bin,
            "parse",
            str(frame_path),
            "--format",
            "json",
            "-o",
            str(ocr_path),
            "--ocr-language",
            language,
            "--quiet",
        ],
        env=env,
    )
    return load_ocr_text(ocr_path)


def main() -> int:
    args = parse_args()
    liteparse_bin = find_liteparse(args.liteparse_bin)
    manifest_path = Path(args.manifest).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    ocr_dir = Path(args.ocr_dir).expanduser().resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    tessdata_prefix = find_tessdata()

    dependency_error: str | None = None
    if not liteparse_bin:
        dependency_error = "liteparse/lit command not found."
    elif not has_imagemagick():
        dependency_error = "ImageMagick is required for LiteParse image input. Install with: brew install imagemagick"
    elif not tessdata_prefix:
        dependency_error = "Tesseract language data not found. Install with: brew install tesseract tesseract-lang"

    if dependency_error and not args.allow_missing_deps:
        raise SystemExit(dependency_error)

    for segment in manifest.get("segments", []):
        for frame in segment.get("frames", []):
            frame_path = Path(frame["path"]).expanduser().resolve()
            ocr_path = ocr_dir / segment["id"] / f"{frame_path.stem}.ocr.json"
            if dependency_error:
                frame["ocr"] = {
                    "status": "missing_dependency",
                    "error": dependency_error,
                    "text": "",
                    "adequate": False,
                    "needs_multimodal": True,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                continue
            try:
                text = ocr_frame(str(liteparse_bin), frame_path, ocr_path, args.ocr_language, tessdata_prefix)
                adequate = len(text) >= args.min_text_chars
                frame["ocr"] = {
                    "status": "done",
                    "path": str(ocr_path),
                    "language": args.ocr_language,
                    "text": text,
                    "char_count": len(text),
                    "adequate": adequate,
                    "needs_multimodal": not adequate,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            except Exception as exc:
                frame["ocr"] = {
                    "status": "error",
                    "error": str(exc),
                    "text": "",
                    "adequate": False,
                    "needs_multimodal": True,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }

    manifest["ocr_policy"] = {
        "tool": "liteparse",
        "tool_path": liteparse_bin,
        "tessdata_prefix": tessdata_prefix,
        "process_model": "one subprocess per frame; no resident OCR service kept in memory",
        "ocr_before_multimodal": True,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
