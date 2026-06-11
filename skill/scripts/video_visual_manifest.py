#!/usr/bin/env python3
"""Build a per-transcript-block visual review manifest."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SEGMENT_RE = re.compile(
    r"^\[(?P<start>\d{2}:\d{2}(?::\d{2})?)-(?P<end>\d{2}:\d{2}(?::\d{2})?)\]\s*(?P<text>.*?)(?=^\[\d{2}:\d{2}(?::\d{2})?-\d{2}:\d{2}(?::\d{2})?\]|\Z)",
    re.MULTILINE | re.DOTALL,
)


@dataclass
class SegmentAnnotation:
    id: str
    start: float
    end: float
    timestamp: str
    transcript: str
    needs_video: bool
    priority: str
    decision_status: str
    rationale: str
    visual_questions: list[str]
    evidence_types: list[str]
    sampling: dict[str, Any]
    ocr_first: bool
    multimodal_if_ocr_weak: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read a video_report.py JSON artifact and create a block-by-block visual review manifest."
    )
    parser.add_argument("report_json", help="Path to video_report.py JSON output.")
    parser.add_argument("-o", "--output", required=True, help="Manifest JSON output path.")
    parser.add_argument(
        "--mode",
        choices=("draft", "blank-review"),
        default="draft",
        help="draft pre-fills conservative annotations; blank-review leaves every block for manual/LLM review.",
    )
    parser.add_argument(
        "--default-max-frames",
        type=int,
        default=3,
        help="Default number of frames to sample for visual segments.",
    )
    return parser.parse_args()


def timestamp_to_seconds(value: str) -> float:
    parts = [int(part) for part in value.split(":")]
    if len(parts) == 2:
        minutes, seconds = parts
        return float(minutes * 60 + seconds)
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return float(hours * 3600 + minutes * 60 + seconds)
    raise ValueError(f"Unsupported timestamp: {value}")


def seconds_to_timestamp(seconds: float) -> str:
    rounded = max(0, int(round(seconds)))
    hours, remainder = divmod(rounded, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def parse_segments(transcript: str) -> list[tuple[float, float, str]]:
    matches = list(SEGMENT_RE.finditer(transcript.strip()))
    if not matches:
        return [(0.0, 0.0, transcript.strip())] if transcript.strip() else []
    segments: list[tuple[float, float, str]] = []
    for match in matches:
        start = timestamp_to_seconds(match.group("start"))
        end = timestamp_to_seconds(match.group("end"))
        text = re.sub(r"\s+", " ", match.group("text")).strip()
        segments.append((start, end, text))
    return segments


def visual_rubric(text: str) -> tuple[bool, str, str, list[str], list[str]]:
    """Produce a conservative draft label from the whole block, not a screenshot trigger."""
    normalized = text.lower()
    digit_count = sum(1 for char in text if char.isdigit())
    cjk_digit_density = digit_count / max(1, len(text))
    evidence_types: list[str] = []
    questions: list[str] = []
    score = 0

    visual_concepts = {
        "matrix": ["矩阵", "支付矩阵", "博弈地图", "五种", "四种状态"],
        "chart": ["曲线", "图", "热力", "指标", "指数", "分位", "散度", "vix", "x "],
        "table": ["清单", "阈值", "比例", "百分", "超过", "低于", "高于"],
        "orderbook": ["订单", "挂单", "撤单", "成交量", "价位", "交易所"],
        "case": ["案例", "案子", "闪电崩盘", "摩根", "gamestop", "gme", "sarao"],
    }
    for evidence_type, terms in visual_concepts.items():
        if any(term in normalized or term in text for term in terms):
            evidence_types.append(evidence_type)
            score += 1

    if cjk_digit_density > 0.025:
        evidence_types.append("dense_numbers")
        score += 1

    if len(text) > 450:
        score += 1

    if "matrix" in evidence_types:
        questions.append("画面是否展示状态矩阵、支付矩阵或分层表格？")
    if "chart" in evidence_types:
        questions.append("画面是否展示曲线、指数、分位数或市场状态图？")
    if "table" in evidence_types or "dense_numbers" in evidence_types:
        questions.append("画面是否有阈值表、数字列表或可 OCR 的关键数值？")
    if "orderbook" in evidence_types:
        questions.append("画面是否展示订单簿、成交量、价位或交易所数据？")
    if "case" in evidence_types:
        questions.append("画面是否展示案例截图、新闻标题、监管文件或历史行情？")

    needs_video = score >= 2
    if score >= 4:
        priority = "high"
    elif score >= 2:
        priority = "medium"
    elif score == 1:
        priority = "low"
    else:
        priority = "none"

    rationale = (
        "Draft label from full-block review: "
        f"score={score}, digit_density={cjk_digit_density:.3f}, evidence={','.join(evidence_types) or 'none'}."
    )
    return needs_video, priority, rationale, questions, sorted(set(evidence_types))


def sample_times(start: float, end: float, max_frames: int) -> list[float]:
    if end <= start:
        return [start]
    duration = end - start
    if max_frames <= 1 or duration < 8:
        return [start + duration / 2]
    points = [start + min(8, duration * 0.2), start + duration / 2, end - min(8, duration * 0.2)]
    unique: list[float] = []
    for point in points[:max_frames]:
        bounded = min(max(start, point), end)
        if all(abs(bounded - existing) > 1 for existing in unique):
            unique.append(bounded)
    return unique


def build_manifest(report: dict[str, Any], *, mode: str, max_frames: int) -> dict[str, Any]:
    annotations: list[SegmentAnnotation] = []
    for index, (start, end, text) in enumerate(parse_segments(report.get("transcript", ""))):
        if mode == "blank-review":
            needs_video = False
            priority = "unreviewed"
            rationale = "Awaiting manual/LLM block-level review."
            questions: list[str] = []
            evidence_types: list[str] = []
            decision_status = "needs_review"
        else:
            needs_video, priority, rationale, questions, evidence_types = visual_rubric(text)
            decision_status = "draft"

        annotations.append(
            SegmentAnnotation(
                id=f"seg_{index:04d}",
                start=start,
                end=end,
                timestamp=f"{seconds_to_timestamp(start)}-{seconds_to_timestamp(end)}",
                transcript=text,
                needs_video=needs_video,
                priority=priority,
                decision_status=decision_status,
                rationale=rationale,
                visual_questions=questions,
                evidence_types=evidence_types,
                sampling={
                    "strategy": "window_sample",
                    "times": [round(value, 3) for value in sample_times(start, end, max_frames)]
                    if needs_video
                    else [],
                    "max_frames": max_frames,
                },
                ocr_first=needs_video,
                multimodal_if_ocr_weak=needs_video,
            )
        )

    return {
        "schema_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_report": report.get("artifacts", {}).get("json"),
        "source": report.get("source"),
        "title": report.get("title"),
        "model": report.get("model"),
        "language": report.get("language"),
        "review_policy": {
            "block_level_decision": True,
            "keyword_triggered_capture": False,
            "ocr_before_multimodal": True,
            "multimodal_only_if_ocr_weak": True,
        },
        "segments": [asdict(annotation) for annotation in annotations],
    }


def main() -> int:
    args = parse_args()
    report_path = Path(args.report_json).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    manifest = build_manifest(report, mode=args.mode, max_frames=args.default_max_frames)
    manifest["source_report"] = str(report_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
