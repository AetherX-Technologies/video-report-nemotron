#!/usr/bin/env python3
"""Render an existing Markdown report with the skill's table/inline renderer."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from video_build_visual_report import build_html, write_pdf


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render an existing Markdown report to HTML/PDF without losing tables or inline Markdown."
    )
    parser.add_argument("markdown", help="Input Markdown path.")
    parser.add_argument("--html", required=True, help="Output HTML path.")
    parser.add_argument("--pdf", default=None, help="Optional output PDF path.")
    parser.add_argument("--title", default=None, help="HTML/PDF title. Defaults to the first Markdown heading.")
    return parser.parse_args()


def infer_title(markdown_text: str, fallback: str) -> str:
    for line in markdown_text.splitlines():
        if line.startswith("# "):
            return line[2:].strip() or fallback
    return fallback


def write_pdf_with_uvx(html_path: Path, pdf_path: Path) -> tuple[bool, str]:
    if shutil.which("uvx") is None:
        return False, "playwright command not found and uvx fallback is unavailable"
    process = subprocess.run(
        [
            "uvx",
            "--from",
            "playwright",
            "playwright",
            "pdf",
            html_path.resolve().as_uri(),
            str(pdf_path),
        ],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.returncode != 0:
        return False, process.stderr.strip() or process.stdout.strip()
    return True, str(pdf_path)


def main() -> int:
    args = parse_args()
    markdown_path = Path(args.markdown).expanduser().resolve()
    html_path = Path(args.html).expanduser().resolve()
    pdf_path = Path(args.pdf).expanduser().resolve() if args.pdf else None

    markdown_text = markdown_path.read_text(encoding="utf-8")
    title = args.title or infer_title(markdown_text, markdown_path.stem)

    html_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {"title": title, "segments": []}
    html_path.write_text(build_html(markdown_text, manifest, html_path, title), encoding="utf-8")

    result = {"markdown": str(markdown_path), "html": str(html_path), "pdf": None}
    if pdf_path:
        ok, message = write_pdf(html_path, pdf_path)
        if not ok and "playwright command not found" in message:
            ok, message = write_pdf_with_uvx(html_path, pdf_path)
        result["pdf"] = str(pdf_path) if ok else None
        result["pdf_status"] = "done" if ok else "error"
        result["pdf_message"] = message

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
