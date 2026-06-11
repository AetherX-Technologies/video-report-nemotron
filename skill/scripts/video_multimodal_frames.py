#!/usr/bin/env python3
"""Run multimodal fallback for frames whose OCR is weak."""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


DEFAULT_BASE_URL = "https://sub2api.gptclubapi.xyz/v1"
DEFAULT_MODEL = "gpt-5.5"


def parse_env_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_env_file(path: Path, *, override: bool = False) -> bool:
    if not path.exists():
        return False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        if override or key not in os.environ:
            os.environ[key] = parse_env_value(value)
    return True


def candidate_env_files(explicit_env_file: str | None) -> list[Path]:
    script_root = Path(__file__).resolve().parents[1]
    candidates: list[Path] = []
    if explicit_env_file:
        candidates.append(Path(explicit_env_file).expanduser())
    candidates.extend(
        [
            Path.cwd() / ".env",
            script_root / ".env",
            Path.home() / ".config" / "video-report-nemotron" / ".env",
        ]
    )
    seen: set[Path] = set()
    unique: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve() if candidate.exists() else candidate
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(candidate)
    return unique


def load_env_from_argv(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--env-file")
    known, _ = parser.parse_known_args(argv)
    for env_path in candidate_env_files(known.env_file):
        load_env_file(env_path)


def parse_args() -> argparse.Namespace:
    load_env_from_argv(sys.argv[1:])
    parser = argparse.ArgumentParser(
        description="Analyze frames with a multimodal model only when OCR is weak or explicitly requested."
    )
    parser.add_argument("manifest", help="Manifest after OCR.")
    parser.add_argument("-o", "--output", required=True, help="Updated manifest output path.")
    parser.add_argument("--env-file", default=None, help="Path to a .env file with OPENAI_* settings.")
    parser.add_argument(
        "--provider",
        choices=("openai", "none"),
        default="openai",
        help="Multimodal provider. Use 'none' to only mark pending frames.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("OPENAI_VISION_MODEL", DEFAULT_MODEL),
        help="Vision-capable model name.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("OPENAI_BASE_URL", DEFAULT_BASE_URL),
        help="OpenAI-compatible API base URL.",
    )
    parser.add_argument("--all", action="store_true", help="Analyze all captured frames, not only weak OCR frames.")
    parser.add_argument("--max-frames", type=int, default=None, help="Maximum frames to analyze in this run.")
    parser.add_argument("--max-output-tokens", type=int, default=700)
    parser.add_argument(
        "--prompt",
        default=None,
        help="Override the default visual analysis prompt.",
    )
    return parser.parse_args()


def image_data_url(path: Path) -> str:
    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{payload}"


def default_prompt(segment: dict[str, Any], frame: dict[str, Any]) -> str:
    questions = "\n".join(f"- {q}" for q in segment.get("visual_questions", [])) or "- Identify useful visual evidence."
    transcript = segment.get("transcript", "")
    ocr_text = frame.get("ocr", {}).get("text", "")
    return f"""You are analyzing one frame from a video report pipeline.

The transcript block may contain ASR errors. Use the image as primary evidence, then use OCR and transcript only as context.

Return concise Chinese notes with:
1. What is visible in the frame.
2. Whether it answers the visual questions.
3. Any key text, numbers, charts, tables, or relationships visible.
4. Whether this frame should be embedded in the final report.

Visual questions:
{questions}

Transcript block:
{transcript[:1800]}

OCR text:
{ocr_text[:1200]}
"""


def extract_response_text(response: dict[str, Any]) -> str:
    output_text = response.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()
    texts: list[str] = []
    for item in response.get("output", []):
        for content in item.get("content", []) if isinstance(item, dict) else []:
            if content.get("type") in {"output_text", "text"} and isinstance(content.get("text"), str):
                texts.append(content["text"])
    return "\n".join(texts).strip()


def analyze_with_openai(
    *,
    frame_path: Path,
    prompt: str,
    model: str,
    base_url: str,
    max_output_tokens: int,
) -> dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    responses_payload = {
        "model": model,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": image_data_url(frame_path)},
                ],
            }
        ],
        "max_output_tokens": max_output_tokens,
    }
    with httpx.Client(timeout=90) as client:
        response = client.post(
            f"{base_url.rstrip('/')}/responses",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=responses_payload,
        )
        if response.status_code in {404, 405}:
            chat_payload = {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": image_data_url(frame_path)}},
                        ],
                    }
                ],
                "max_tokens": max_output_tokens,
            }
            response = client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=chat_payload,
            )
    if response.status_code >= 400:
        raise RuntimeError(f"OpenAI API error {response.status_code}: {response.text[:1000]}")
    data = response.json()
    text = extract_response_text(data)
    if not text and isinstance(data.get("choices"), list):
        texts = []
        for choice in data["choices"]:
            message = choice.get("message", {}) if isinstance(choice, dict) else {}
            content = message.get("content")
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and isinstance(item.get("text"), str):
                        texts.append(item["text"])
        text = "\n".join(texts).strip()
    if not text:
        raise RuntimeError("OpenAI response did not contain output text.")
    return {
        "status": "done",
        "provider": "openai",
        "model": model,
        "base_url": base_url,
        "text": text,
        "response_id": data.get("id"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def should_analyze(frame: dict[str, Any], include_all: bool) -> bool:
    if include_all:
        return True
    ocr = frame.get("ocr", {})
    return bool(ocr.get("needs_multimodal")) or ocr.get("status") in {"error", "missing_dependency"}


def main() -> int:
    args = parse_args()
    manifest_path = Path(args.manifest).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    analyzed = 0
    for segment in manifest.get("segments", []):
        for frame in segment.get("frames", []):
            if not should_analyze(frame, args.all):
                continue
            if args.max_frames is not None and analyzed >= args.max_frames:
                continue
            frame_path = Path(frame["path"]).expanduser().resolve()
            prompt = args.prompt or default_prompt(segment, frame)
            if args.provider == "none":
                frame["vision"] = {
                    "status": "pending",
                    "provider": "none",
                    "reason": "Multimodal fallback not run.",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            else:
                try:
                    frame["vision"] = analyze_with_openai(
                        frame_path=frame_path,
                        prompt=prompt,
                        model=args.model,
                        base_url=args.base_url,
                        max_output_tokens=args.max_output_tokens,
                    )
                except Exception as exc:
                    frame["vision"] = {
                        "status": "error",
                        "provider": args.provider,
                        "model": args.model,
                        "base_url": args.base_url,
                        "error": str(exc),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
            analyzed += 1

    manifest["multimodal_policy"] = {
        "provider": args.provider,
        "model": args.model if args.provider != "none" else None,
            "base_url": args.base_url if args.provider != "none" else None,
        "only_when_ocr_weak": not args.all,
        "frames_analyzed_this_run": analyzed,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
