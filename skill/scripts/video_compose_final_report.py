#!/usr/bin/env python3
"""Compose a user-facing long-form report from transcript and visual evidence."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from video_build_visual_report import (
    build_html,
    evidence_summary,
    frame_visual_note,
    parse_timestamp_start,
    relpath,
    score_frame,
    write_pdf,
)
from video_multimodal_frames import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    extract_response_text,
    language_instruction,
    load_env_from_argv,
)


SEGMENT_RE = re.compile(
    r"^\[(?P<start>\d{2}:\d{2}(?::\d{2})?)-(?P<end>\d{2}:\d{2}(?::\d{2})?)\]\s*(?P<text>.*?)(?=^\[\d{2}:\d{2}(?::\d{2})?-\d{2}:\d{2}(?::\d{2})?\]|\Z)",
    re.MULTILINE | re.DOTALL,
)
INTERNAL_TERMS_RE = re.compile(
    r"\b(manifest|OCR|ASR|Draft label|score=|digit_density|Transcript block|Visual questions)\b|"
    r"转写块|证据目录|视觉证据|多模态|审计|需要配图|适合插入|适合放入|适合承接|配图应"
)


@dataclass
class TranscriptSegment:
    index: int
    timestamp: str
    start: int
    text: str


@dataclass
class ImageItem:
    id: str
    path: str
    caption: str
    note: str
    timestamp: str
    start: int
    segment_text: str


def parse_args() -> argparse.Namespace:
    load_env_from_argv(sys.argv[1:])
    parser = argparse.ArgumentParser(description="Compose the final user-facing report.")
    parser.add_argument("report_json", help="video_report.py JSON output.")
    parser.add_argument("visual_manifest", help="Manifest after OCR/multimodal visual processing.")
    parser.add_argument("--markdown", required=True, help="Final Markdown output path.")
    parser.add_argument("--html", required=True, help="Final HTML output path.")
    parser.add_argument("--pdf", default=None, help="Optional final PDF output path.")
    parser.add_argument("--title", default=None, help="Final report title.")
    parser.add_argument("--env-file", default=None, help="Path to a .env file with OPENAI_* settings.")
    parser.add_argument("--provider", choices=("openai", "none"), default="openai")
    parser.add_argument(
        "--model",
        default=os.environ.get("OPENAI_TEXT_MODEL")
        or os.environ.get("OPENAI_VISION_MODEL")
        or DEFAULT_MODEL,
        help="OpenAI-compatible text model.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("OPENAI_BASE_URL", DEFAULT_BASE_URL),
        help="OpenAI-compatible API base URL.",
    )
    parser.add_argument("--max-images", type=int, default=9)
    parser.add_argument("--max-output-tokens", type=int, default=12000)
    parser.add_argument("--transcript-char-limit", type=int, default=90000)
    parser.add_argument("--llm-chunk-chars", type=int, default=2600)
    parser.add_argument(
        "--report-language",
        default=os.environ.get("REPORT_LANGUAGE", "auto"),
        help="Final report language: auto, en, zh-CN, ja, etc.",
    )
    return parser.parse_args()


def plain_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def strip_timestamp_labels(value: str) -> str:
    return re.sub(r"\[\d{2}:\d{2}(?::\d{2})?-\d{2}:\d{2}(?::\d{2})?\]\s*", "", value)


def parse_segments(transcript: str) -> list[TranscriptSegment]:
    matches = list(SEGMENT_RE.finditer(transcript.strip()))
    if not matches:
        body = plain_text(transcript)
        return [TranscriptSegment(index=0, timestamp="", start=0, text=body)] if body else []
    segments: list[TranscriptSegment] = []
    for index, match in enumerate(matches):
        timestamp = f"{match.group('start')}-{match.group('end')}"
        segments.append(
            TranscriptSegment(
                index=index,
                timestamp=timestamp,
                start=parse_timestamp_start(timestamp),
                text=plain_text(match.group("text")),
            )
        )
    return segments


def is_chinese_language(language: str | None) -> bool:
    normalized = (language or "").lower()
    return normalized.startswith("zh")


def language_char_counts(text: str) -> tuple[int, int]:
    return (
        len(re.findall(r"[\u4e00-\u9fff]", text)),
        len(re.findall(r"[A-Za-z]", text)),
    )


def first_heading(markdown: str) -> str:
    for line in markdown.splitlines():
        if line.startswith("# "):
            return line
    return ""


def language_compliance_errors(markdown: str, report_language: str) -> list[str]:
    text = plain_text(markdown)
    chinese_chars, latin_chars = language_char_counts(text)
    errors: list[str] = []
    if is_chinese_language(report_language):
        if len(text) > 1500 and latin_chars > 0 and chinese_chars < max(300, latin_chars // 2):
            errors.append(
                f"final report does not look like requested Chinese output: "
                f"{chinese_chars} CJK chars, {latin_chars} Latin chars"
            )
        return errors

    if re.search(r"[\u4e00-\u9fff]", first_heading(markdown)):
        errors.append("final report title is not in the requested non-Chinese output language")
    if chinese_chars > 80 and chinese_chars > max(30, latin_chars // 50):
        errors.append(
            f"final report contains too much Chinese for requested non-Chinese output: "
            f"{chinese_chars} CJK chars, {latin_chars} Latin chars"
        )
    return errors


def infer_report_language(report: dict[str, Any], requested_language: str | None) -> str:
    requested = (requested_language or "auto").strip()
    if requested and requested != "auto":
        return requested
    language = str(report.get("language") or "").strip()
    if language:
        return language
    transcript = str(report.get("transcript") or "")
    sample = transcript[:6000]
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", sample))
    latin_chars = len(re.findall(r"[A-Za-z]", sample))
    if chinese_chars > max(20, latin_chars // 3):
        return "zh-CN"
    return "en"


def infer_report_title(report: dict[str, Any], manifest: dict[str, Any], report_language: str = "auto") -> str:
    candidate = str(report.get("title") or manifest.get("title") or "").strip()
    if candidate and not re.fullmatch(r"[A-Za-z0-9_-]{6,}", candidate):
        return f"{candidate} 深度报告" if is_chinese_language(report_language) else f"{candidate} Deep Report"
    source = str(report.get("source") or manifest.get("source") or "").strip()
    if source:
        return "视频内容深度报告" if is_chinese_language(report_language) else "Video Content Deep Report"
    return "内容深度报告" if is_chinese_language(report_language) else "Content Deep Report"


def localized_evidence_summary(evidence_types: list[str], report_language: str | None) -> str:
    if is_chinese_language(report_language):
        return evidence_summary(evidence_types)
    labels = {
        "matrix": "matrix or state framework",
        "chart": "chart or metric view",
        "table": "table or threshold list",
        "dense_numbers": "dense numerical display",
        "orderbook": "order book or trading data",
        "case": "case material",
    }
    rendered = [labels[item] for item in evidence_types if item in labels]
    return ", ".join(rendered) if rendered else "video frame"


def clean_visual_note(note: str, evidence_types: list[str], report_language: str = "auto") -> str:
    text = plain_text(note)
    if not is_chinese_language(report_language):
        fallback = f"This image shows {localized_evidence_summary(evidence_types, report_language)} that supports the surrounding discussion."
        if not text or "不建议嵌入" in text or INTERNAL_TERMS_RE.search(text) or re.search(r"[\u4e00-\u9fff]", text):
            return fallback[:240]
        replacements = {
            "This frame": "This image",
            "this frame": "this image",
            "The frame": "The image",
            "the frame": "the image",
            "visual evidence": "image detail",
            "Visual evidence": "Image detail",
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
        text = re.sub(r"\b(OCR|ASR|manifest|Transcript block|Visual questions)\b[^.;]*[.;]?", "", text, flags=re.IGNORECASE)
        text = plain_text(text)
        return (text or fallback)[:240]

    if not text or "不建议嵌入" in text:
        text = f"画面包含{localized_evidence_summary(evidence_types, report_language)}。"
    replacements = {
        "该帧": "这张图",
        "此帧": "这张图",
        "OCR": "画面文字",
        "ocr": "画面文字",
        "多模态": "图像复核",
        "适合作为本节视觉证据": "对应本节讨论的画面信息",
        "适合作为相关论点的视觉补充": "对应正文讨论的画面信息",
        "视觉证据": "画面信息",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r"详细[^。；;]*(证据文件|审计|manifest)[^。；;]*[。；;]?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(score|digit_density|Draft label|Transcript block|Visual questions)\b[^。；;]*[。；;]?", "", text)
    text = plain_text(text)
    return (text or f"画面包含{localized_evidence_summary(evidence_types, report_language)}。")[:240]


def image_caption(segment: dict[str, Any], frame: dict[str, Any], index: int, report_language: str = "auto") -> str:
    evidence_types = list(segment.get("evidence_types", []))
    evidence = localized_evidence_summary(evidence_types, report_language)
    if not is_chinese_language(report_language):
        if evidence == "video frame":
            return f"Figure {index}: Video frame"
        return f"Figure {index}: {evidence.capitalize()}"
    if evidence == "视频画面证据":
        return f"图 {index}：视频画面"
    return f"图 {index}：{evidence}"


def select_report_frames(manifest: dict[str, Any], max_images: int) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    selected: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for segment in manifest.get("segments", []):
        frames = segment.get("frames", [])
        if not segment.get("needs_video") or not frames:
            continue
        selected.append((segment, max(frames, key=lambda frame: score_frame(segment, frame))))
    selected.sort(key=lambda item: parse_timestamp_start(item[0].get("timestamp")))
    if len(selected) <= max_images:
        return selected

    indexes = {0, len(selected) - 1}
    if max_images > 2:
        step = (len(selected) - 1) / (max_images - 1)
        indexes.update(round(step * index) for index in range(max_images))
    return [item for index, item in enumerate(selected) if index in indexes][:max_images]


def build_image_bank(
    manifest: dict[str, Any],
    markdown_path: Path,
    max_images: int,
    report_language: str = "auto",
) -> list[dict[str, str]]:
    bank: list[dict[str, str]] = []
    for index, (segment, frame) in enumerate(select_report_frames(manifest, max_images), start=1):
        evidence_types = list(segment.get("evidence_types", []))
        bank.append(
            {
                "id": f"img_{index:02d}",
                "path": relpath(frame["path"], markdown_path.parent),
                "caption": image_caption(segment, frame, index, report_language),
                "note": clean_visual_note(frame_visual_note(segment, frame), evidence_types, report_language),
                "timestamp": str(segment.get("timestamp", "")),
                "start": str(parse_timestamp_start(segment.get("timestamp"))),
                "segment_text": plain_text(str(segment.get("transcript", "")))[:1000],
            }
        )
    return bank


def image_bank_prompt(image_bank: list[dict[str, str]]) -> str:
    if not image_bank:
        return "No images available."
    lines = []
    for image in image_bank:
        lines.append(
            f"- {image['id']}: caption={image['caption']}; note={image['note']}; "
            f"nearby transcript={image['segment_text'][:360]}. Use [IMAGE:{image['id']}] exactly once where it naturally supports the content."
        )
    return "\n".join(lines)


def report_language_instruction(report_language: str | None) -> str:
    return language_instruction(report_language)


def localized_source_label(report_language: str | None) -> str:
    return "原视频" if is_chinese_language(report_language) else "Source video"


def localized_note(report_language: str | None) -> str:
    if is_chinese_language(report_language):
        return "说明：本报告按主题重组视频内容，保留主要细节、数字、案例和提醒；不构成投资建议。"
    return "Note: this report reorganizes the video by topic while preserving key details, numbers, cases, constraints, and caveats. It is not financial advice."


def localized_usage_heading(report_language: str | None) -> str:
    return "使用提醒" if is_chinese_language(report_language) else "Usage Notes"


def localized_reader_heading(report_language: str | None) -> str:
    return "读者使用提醒" if is_chinese_language(report_language) else "Reader Notes"


def localized_core_heading(report_language: str | None) -> str:
    return "核心结论" if is_chinese_language(report_language) else "Core Takeaways"


def localized_claims_heading(report_language: str | None) -> str:
    return "关键论点" if is_chinese_language(report_language) else "Key Claims"


def localized_details_heading(report_language: str | None) -> str:
    return "重要数字、案例与限定条件" if is_chinese_language(report_language) else "Important Numbers, Cases, and Constraints"


def localized_images_heading(report_language: str | None) -> str:
    return "图像补充" if is_chinese_language(report_language) else "Additional Images"


def build_prompt(
    report: dict[str, Any],
    manifest: dict[str, Any],
    image_bank: list[dict[str, str]],
    title: str,
    limit: int,
    report_language: str = "auto",
) -> str:
    transcript = str(report.get("transcript", ""))[:limit]
    source = report.get("source") or manifest.get("source")
    if is_chinese_language(report_language):
        return f"""你是一名中文深度报告作者。请把下面的视频转写整理成一份可直接交给用户阅读的完整图文报告。

硬性要求：
- 报告必须比原视频更清晰，但不能少掉原视频里的实质信息、例子、数字、阈值、案例、限定条件和提醒。
- 只能删除口头禅、重复语、寒暄、订阅引导和明显无意义的转写噪声；不要把细节压缩成几条概括。
- 不要按时间轴逐段复述，要按主题重组；但每个主题下要保留原视频的细节密度。
- 可以在原文基础上解释“为什么重要、怎么理解、和其它点有什么关系”，但不能凭空新增原视频没有支持的事实。
- 不要出现内部流程词或编辑说明，例如 manifest、OCR、ASR、转写块、证据目录、视觉问题、需要配图、适合插入、这一段配图。
- 图片必须自然嵌入正文：只使用给定占位符 [IMAGE:img_XX]，不要编造图片。图片前后写内容解读，不写“为什么这里要配图”。
- 所有图文说明都要服务于视频内容本身。
- 输出中文 Markdown，不构成投资建议。

建议结构：
1. 核心结论：先讲这期视频到底在说什么。
2. 主题化正文：每个主题都要有解释、原文细节、数字/案例/限定条件。
3. 关键图像：把图片嵌入最相关主题附近。
4. 读者使用提醒：说明这些内容应该如何理解，不能怎么误用。

报告标题：{title}
原视频：{source}

可用图片：
{image_bank_prompt(image_bank)}

视频转写内容：
{transcript}
"""
    return f"""You are a long-form report writer. Turn the video transcript below into a complete, user-facing illustrated report.

Output language: {report_language_instruction(report_language)}

Hard requirements:
- If the transcript is not in the output language, translate and explain it in the output language. Do not leave source-language prose in the report.
- Translate a non-output-language video title into the output language in the final title.
- The report must be clearer than the original video, but must not drop substantive information, examples, numbers, thresholds, case names, dates, constraints, caveats, or warnings from the video.
- Remove filler, repeated phrases, greetings, subscription prompts, and obvious transcription noise, but do not compress the content into a short summary.
- Reorganize by topic instead of replaying the timeline block by block.
- Do not expose internal pipeline terms or editorial notes such as manifest, OCR, ASR, transcript block, evidence directory, visual questions, needs image, suitable image, insert image here, screenshot rationale, or similar wording.
- Images must be integrated naturally into the report. Use only the provided placeholders like [IMAGE:img_XX]. Do not invent images.
- Text before and after images should explain the video content itself, not why an image was selected.
- All figure captions and surrounding text must serve the final reader.
- Output Markdown. Do not provide investment, medical, legal, or other professional advice.

Report title: {title}
Source video: {source}

Available images:
{image_bank_prompt(image_bank)}

Video transcript:
{transcript}
"""


def extract_chat_text(data: dict[str, Any]) -> str:
    if isinstance(data.get("choices"), list):
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
        return "\n".join(texts).strip()
    return ""


def call_openai_text(prompt: str, *, model: str, base_url: str, max_output_tokens: int) -> str:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    with httpx.Client(timeout=180) as client:
        response = client.post(
            f"{base_url.rstrip('/')}/responses",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
                "max_output_tokens": max_output_tokens,
            },
        )
        if response.status_code in {404, 405, 408, 429, 500, 502, 503, 504, 520, 522, 524}:
            response = client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_output_tokens,
                },
            )
    if response.status_code >= 400:
        raise RuntimeError(f"OpenAI API error {response.status_code}: {response.text[:1000]}")
    data = response.json()
    text = extract_response_text(data) or extract_chat_text(data)
    if not text:
        raise RuntimeError("OpenAI response did not contain output text.")
    return text.strip()


def strip_markdown_fence(text: str) -> str:
    stripped = text.strip()
    match = re.fullmatch(r"```(?:markdown|md)?\s*(.*?)\s*```", stripped, flags=re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else stripped


def rewrite_report_language(
    markdown: str,
    *,
    report_language: str,
    model: str,
    base_url: str,
    max_output_tokens: int,
) -> str:
    target = "Simplified Chinese" if is_chinese_language(report_language) else report_language_instruction(report_language)
    extra = (
        "Translate every non-Chinese explanatory sentence into natural Simplified Chinese while preserving standard product names."
        if is_chinese_language(report_language)
        else (
            "Translate every Chinese sentence, heading, title fragment, figure note, and bullet into natural English. "
            "Do not leave Chinese text in the title or body. Translate the video title by meaning if needed."
        )
    )
    prompt = f"""Rewrite the following Markdown report so the entire user-facing report is in {target}.

Hard requirements:
- Preserve all substantive details, examples, caveats, numbers, and the existing section hierarchy.
- Preserve Markdown image links and keep each image near the same topic.
- Preserve the source URL and advisory/disclaimer meaning.
- Do not summarize or shorten the report.
- Do not mention translation, rewriting, internal tools, OCR, ASR, manifests, or pipeline details.
- {extra}

Markdown report to rewrite:
{markdown}
"""
    rewritten = call_openai_text(
        prompt,
        model=model,
        base_url=base_url,
        max_output_tokens=max_output_tokens,
    )
    return strip_markdown_fence(rewritten).strip() + "\n"


def chunk_segments(segments: list[TranscriptSegment], max_chars: int) -> list[list[TranscriptSegment]]:
    chunks: list[list[TranscriptSegment]] = []
    current: list[TranscriptSegment] = []
    current_len = 0
    for segment in segments:
        segment_len = len(segment.text)
        if current and current_len + segment_len > max_chars:
            chunks.append(current)
            current = []
            current_len = 0
        current.append(segment)
        current_len += segment_len
    if current:
        chunks.append(current)
    return chunks


def chunk_prompt(
    title: str,
    source: str,
    chunk: list[TranscriptSegment],
    chunk_index: int,
    chunk_count: int,
    report_language: str = "auto",
) -> str:
    transcript = "\n\n".join(
        f"[{item.timestamp}] {item.text}" if item.timestamp else item.text
        for item in chunk
    )
    if is_chinese_language(report_language):
        return f"""你是一名中文深度报告作者。下面是同一个视频的第 {chunk_index}/{chunk_count} 部分转写。

请把这一部分整理成最终报告中的一个或多个章节，要求：
- 保留这一部分里的实质信息：论点、例子、数字、比例、阈值、案例名称、时间、限制条件和提醒。
- 只能删口头禅、重复语、订阅引导和明显无意义的转写噪声；不要删细节。
- 可以在原文基础上做解释和展开，但不能凭空新增与原文无关的事实。
- 不要写“本段”“该转写”“时间轴”“需要配图”“适合插图”等编辑过程语言。
- 不要出现 manifest、OCR、ASR、证据目录、视觉问题等内部流程词。
- 输出中文 Markdown，使用 `##` 和 `###` 组织；不要写总标题。
- 这只是整份报告的一部分，不要写最终总结，除非原文这一部分本身就在总结。

报告标题：{title}
原视频：{source}

转写：
{transcript}
"""
    return f"""You are writing one part of a final long-form video report. This is transcript chunk {chunk_index}/{chunk_count} from the same video.

Output language: {report_language_instruction(report_language)}

Turn this chunk into one or more final-report sections:
- If this transcript chunk is not in the output language, translate and explain it in the output language. Do not leave source-language prose in the section.
- Preserve all substantive information in this chunk: claims, examples, numbers, ratios, thresholds, case names, dates, constraints, caveats, and warnings.
- Remove only filler, repetition, subscription prompts, and obvious transcription noise. Do not drop details.
- You may clarify and explain ideas from the transcript, but do not invent unrelated facts.
- Do not write editorial process language such as "this segment", "this transcript", "timeline", "needs image", "suitable image", or "insert image".
- Do not expose internal terms such as manifest, OCR, ASR, evidence directory, or visual questions.
- Output Markdown with `##` and `###` headings. Do not write the document title.
- This is only part of the whole report; do not write a final conclusion unless this chunk itself is concluding.

Report title: {title}
Source video: {source}

Transcript:
{transcript}
"""


def compose_with_openai(
    report: dict[str, Any],
    image_bank: list[dict[str, str]],
    title: str,
    *,
    model: str,
    base_url: str,
    max_output_tokens: int,
    chunk_chars: int,
    transcript_limit: int,
    report_language: str,
) -> str:
    transcript = str(report.get("transcript", ""))[:transcript_limit]
    segments = parse_segments(transcript)
    if not segments:
        raise RuntimeError("Transcript is empty.")
    chunks = chunk_segments(segments, chunk_chars)
    source = str(report.get("source") or "")
    sections: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        prompt = chunk_prompt(title, source, chunk, index, len(chunks), report_language)
        section = call_openai_text(
            prompt,
            model=model,
            base_url=base_url,
            max_output_tokens=max(2500, min(max_output_tokens, 5000)),
        )
        sections.append(section.strip())

    lines = [
        f"# {title}",
        "",
        f"> {localized_source_label(report_language)}: {source}  ",
        f"> {localized_note(report_language)}",
        "",
    ]
    used_images: set[str] = set()
    images = [
        ImageItem(
            id=item["id"],
            path=item["path"],
            caption=item["caption"],
            note=item["note"],
            timestamp=item.get("timestamp", ""),
            start=int(item.get("start") or 0),
            segment_text=item.get("segment_text", ""),
        )
        for item in image_bank
    ]
    for section, chunk in zip(sections, chunks):
        lines.extend([section, ""])
        image = nearest_image(images, used_images, chunk[0].start)
        if image:
            lines.extend(render_image_placeholder(image, report_language))
    lines.extend(
        [
            f"## {localized_usage_heading(report_language)}",
            "",
            "以上内容整理自视频本身。涉及市场、投资、风险或决策时，应结合原始资料、数据源和个人约束复核；本报告不构成投资建议。"
            if is_chinese_language(report_language)
            else "This report is based on the video itself. For markets, investments, risk, or decisions, verify against primary sources, data, and your own constraints. This report is not financial advice.",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def keywords_from_text(text: str) -> list[str]:
    candidates = re.findall(r"[A-Za-z][A-Za-z0-9.+#-]{1,}|[\u4e00-\u9fff]{2,8}|\d+(?:\.\d+)?%?", text)
    stopwords = {
        "这个",
        "那个",
        "就是",
        "如果",
        "因为",
        "所以",
        "但是",
        "一个",
        "一种",
        "什么",
        "大家",
        "市场",
        "视频",
        "时候",
        "可以",
        "需要",
        "我们",
        "他们",
        "起来",
        "其实",
        "非常",
        "以及",
        "进行",
        "出现",
        "说明",
        "状态",
    }
    seen: set[str] = set()
    keywords: list[str] = []
    for item in candidates:
        token = item.strip()
        if token.lower() in seen or token in stopwords:
            continue
        if token.isdigit() and len(token) < 2:
            continue
        seen.add(token.lower())
        keywords.append(token)
        if len(keywords) >= 12:
            break
    return keywords


def segment_heading(segment: TranscriptSegment) -> str:
    text = segment.text
    keywords = keywords_from_text(text)
    if keywords:
        return "、".join(keywords[:4])
    return f"主题 {segment.index + 1}"


def group_segments(segments: list[TranscriptSegment]) -> list[tuple[str, list[TranscriptSegment]]]:
    groups: list[tuple[str, list[TranscriptSegment]]] = []
    for segment in segments:
        heading = segment_heading(segment)
        if groups and groups[-1][0] == heading:
            groups[-1][1].append(segment)
        else:
            groups.append((heading, [segment]))
    return groups


def compress_asr_text(text: str) -> str:
    text = strip_timestamp_labels(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def paragraphize(text: str, max_chars: int = 520) -> list[str]:
    text = compress_asr_text(text)
    if len(text) <= max_chars:
        return [text] if text else []
    sentences = re.split(r"(?<=[。！？.!?])\s+", text)
    if len(sentences) == 1:
        sentences = re.split(r"(?<=[。！？!?])\s*", text)
    paragraphs: list[str] = []
    current = ""
    for sentence in sentences:
        if not sentence:
            continue
        if current and len(current) + len(sentence) > max_chars:
            paragraphs.append(current.strip())
            current = sentence
        else:
            current += sentence
    if current.strip():
        paragraphs.append(current.strip())
    return paragraphs


def build_opening_summary(segments: list[TranscriptSegment]) -> str:
    full_text = compress_asr_text(" ".join(segment.text for segment in segments))
    first_keywords = keywords_from_text(full_text)
    if not first_keywords:
        return "本报告按照视频原始内容重组为主题化结构，保留主要论点、案例、数字和提醒。"
    return (
        "本报告按照视频原始内容重组为主题化结构，重点保留并解释以下线索："
        + "、".join(first_keywords[:10])
        + "。"
    )


def build_opening_summary_en(segments: list[TranscriptSegment]) -> str:
    full_text = compress_asr_text(" ".join(segment.text for segment in segments))
    first_keywords = keywords_from_text(full_text)
    if not first_keywords:
        return "This report reorganizes the original video into a topic-driven structure while preserving the main claims, examples, numbers, and caveats."
    return (
        "This report reorganizes the original video into a topic-driven structure, focusing on these recurring signals: "
        + ", ".join(first_keywords[:10])
        + "."
    )


def extract_detail_items(text: str, limit: int = 18) -> list[str]:
    text = compress_asr_text(text)
    candidates: list[str] = []
    for sentence in re.split(r"(?<=[。！？!?])\s*", text):
        sentence = plain_text(sentence)
        if not sentence:
            continue
        has_number = bool(re.search(r"\d", sentence))
        has_marker = any(marker in sentence for marker in ("案例", "指标", "阈值", "超过", "低于", "高于", "比例", "风险", "信号", "结论", "提醒", "注意"))
        if has_number or has_marker:
            candidates.append(sentence)
        if len(candidates) >= limit:
            break
    return candidates


def extract_core_claims(segments: list[TranscriptSegment], limit: int = 8) -> list[str]:
    claims: list[str] = []
    for segment in segments:
        for sentence in re.split(r"(?<=[。！？.!?])\s+", compress_asr_text(segment.text)):
            sentence = plain_text(sentence)
            if not sentence:
                continue
            if any(marker in sentence for marker in ("结论", "核心", "关键", "本质", "问题", "意味着", "记住", "真正", "不是", "而是")):
                claims.append(sentence)
            if len(claims) >= limit:
                return claims
    return claims


def contains_term(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def chinese_theme_expansions(text: str) -> list[tuple[str, str]]:
    rules: list[tuple[str, tuple[str, ...], str]] = [
        (
            "支付矩阵与博弈框架",
            ("支付矩阵", "博弈"),
            "视频把市场理解为一个参与者相互影响的博弈系统，而不是单点预测问题。支付矩阵的意义在于把不同状态下各类参与者的收益、风险和行为激励放到同一个框架里看：谁在某种状态下受益，谁在某种状态下承担损失，哪些行为会放大波动，哪些行为只是结果而不是原因。用这个框架读视频时，重点不是记住一个结论，而是理解博主如何把状态、参与者和收益窗口连起来。",
        ),
        (
            "波动率、期权与风险状态",
            ("VIX", "期权", "波动"),
            "VIX、期权和波动率相关内容在视频里承担的是风险状态指示器的角色。它们不是孤立指标，而是用来观察市场是否从普通回调进入更极端的压力环境：当波动率、期权定价和情绪变化一起出现时，视频试图说明市场可能已经进入不同的状态区间，参与者的行为也会随之改变。",
        ),
        (
            "文本情绪、政策新颖度与叙事冲击",
            ("NLP", "情绪", "政策", "新颖度", "政策焦虑"),
            "视频提到 NLP、情绪分、政策新颖度和政策焦虑度，是在把文本信息转成可比较的风险信号。这里的重点不是模型本身，而是信息环境如何改变市场：政策表达越陌生、情绪越负面、焦虑度越高，越可能触发参与者重新定价风险。报告中保留这些细节，是因为它们解释了为什么同样的价格变化在不同信息背景下含义不同。",
        ),
        (
            "订单、撤单、挂单与微观结构异常",
            ("订单", "撤单", "挂单", "spoofing"),
            "订单、撤单、挂单和 spoofing 相关内容指向市场微观结构。视频并不是只看成交价，而是关注订单簿里尚未成交、快速变化或可能带有诱导性的行为。尤其当撤单量远高于成交量时，视频将其视为流动性质量和真实交易意愿的线索：表面上有挂单，不等于市场真的有稳定承接。",
        ),
        (
            "极端事件与案例参照",
            ("闪电崩盘", "GameStop", "2020", "3 月 16", "3月16"),
            "闪电崩盘、GameStop 和具体日期案例用于说明这些指标不是抽象概念，而是可以放到真实极端事件中观察。视频通过这些案例提醒读者：当流动性、情绪、波动率和订单行为同时异常时，市场状态可能已经从常规波动变成结构性压力。案例的价值在于帮助读者识别模式，而不是把某一次历史事件机械套用到所有行情。",
        ),
        (
            "价格分布、整数价位与异常检测",
            ("Benford", "整数价位", "价格分布"),
            "Benford、整数价位等内容体现的是异常检测思路。视频试图从价格或订单分布里寻找不自然的集中、偏移或人为痕迹。这样的信号通常不能单独给出结论，但可以作为复核入口：当它和撤单、热点传播、情绪变化等线索一起出现时，才更值得重视。",
        ),
        (
            "稳定币、热点传播与收益窗口",
            ("稳定币", "热点传播", "收益窗口"),
            "稳定币、热点传播和收益窗口把资金、叙事和时间维度连在一起。视频关心的不只是某个热点有没有出现，而是热点扩散到什么阶段、资金通道是否配合、机会窗口是否已经被压缩。这个角度强调的是节奏：过早可能缺少确认，过晚可能只剩拥挤交易。",
        ),
    ]
    expansions: list[tuple[str, str]] = []
    for heading, terms, paragraph in rules:
        if contains_term(text, terms):
            expansions.append((heading, paragraph))
    return expansions


def build_detail_commentary(detail: str) -> str:
    commentary = "这个细节的作用是把视频中的判断落到可核对的线索上。"
    if re.search(r"\d", detail):
        commentary += "数字、日期或比例让判断不只是情绪化描述，而是可以和其它指标并列比较。"
    if any(term in detail for term in ("VIX", "情绪", "政策", "撤单", "成交量", "焦虑度")):
        commentary += "它还提示读者要同时看状态、行为和信息环境，而不是只看单一价格结果。"
    return commentary


def nearest_image(images: list[ImageItem], used: set[str], start: int) -> ImageItem | None:
    candidates = [image for image in images if image.id not in used]
    if not candidates:
        return None
    image = min(candidates, key=lambda item: abs(item.start - start))
    used.add(image.id)
    return image


def render_image_placeholder(image: ImageItem, report_language: str = "auto") -> list[str]:
    separator = "。" if is_chinese_language(report_language) else ". "
    return [f"[IMAGE:{image.id}]", "", f"*{image.caption}{separator}{image.note}*", ""]


def deterministic_report(
    report: dict[str, Any],
    image_bank: list[dict[str, str]],
    title: str,
    report_language: str = "auto",
) -> str:
    report_language = infer_report_language(report, report_language)
    segments = parse_segments(str(report.get("transcript", "")))
    images = [
        ImageItem(
            id=item["id"],
            path=item["path"],
            caption=item["caption"],
            note=item["note"],
            timestamp=item.get("timestamp", ""),
            start=int(item.get("start") or 0),
            segment_text=item.get("segment_text", ""),
        )
        for item in image_bank
    ]
    used_images: set[str] = set()
    source = report.get("source") or ""
    lines = [
        f"# {title}",
        "",
        f"> {localized_source_label(report_language)}: {source}  ",
        f"> {localized_note(report_language)}",
        "",
        f"## {localized_core_heading(report_language)}",
        "",
        build_opening_summary(segments) if is_chinese_language(report_language) else build_opening_summary_en(segments),
        "",
    ]
    if images:
        first_image = nearest_image(images, used_images, segments[0].start if segments else 0)
        if first_image:
            lines.extend(render_image_placeholder(first_image, report_language))

    claims = extract_core_claims(segments)
    if claims:
        lines.extend([f"## {localized_claims_heading(report_language)}", ""])
        for claim in claims:
            lines.extend([f"- {claim}", ""])

    details = extract_detail_items(" ".join(segment.text for segment in segments))
    if details:
        lines.extend([f"## {localized_details_heading(report_language)}", ""])
        for detail in details:
            lines.extend([f"- {detail}", ""])
            if is_chinese_language(report_language):
                lines.extend([f"  {build_detail_commentary(detail)}", ""])

    full_body = " ".join(segment.text for segment in segments)
    if is_chinese_language(report_language):
        expansions = chinese_theme_expansions(full_body)
        if expansions:
            lines.extend(["## 主题拆解", ""])
            for heading, paragraph in expansions:
                lines.extend([f"### {heading}", "", paragraph, ""])

    groups = group_segments(segments)
    for index, (heading, items) in enumerate(groups, start=1):
        lines.extend([f"## {index}. {heading}", ""])
        body = " ".join(item.text for item in items)
        for paragraph in paragraphize(body):
            lines.extend([paragraph, ""])
        group_start = items[0].start if items else 0
        image = nearest_image(images, used_images, group_start)
        if image:
            lines.extend(render_image_placeholder(image, report_language))

    lines.extend(
        [
            f"## {localized_reader_heading(report_language)}",
            "",
            "以上内容来自视频本身的论证、案例和数字。实际使用时，应把它当作理解框架和风险检查清单，而不是自动交易或自动决策指令。"
            if is_chinese_language(report_language)
            else "The material above comes from the video's arguments, examples, and numbers. Treat it as an interpretation framework and review checklist, not as an automated trading or decision instruction.",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def render_images(markdown: str, image_bank: list[dict[str, str]], report_language: str = "auto") -> str:
    rendered = markdown
    used: set[str] = set()
    for image in image_bank:
        placeholder = f"[IMAGE:{image['id']}]"
        figure = f"![{image['caption']}]({image['path']})"
        if placeholder in rendered:
            rendered = rendered.replace(placeholder, figure)
            used.add(image["id"])
    if image_bank and not used:
        appendix = ["", f"## {localized_images_heading(report_language)}", ""]
        for image in image_bank[:6]:
            separator = "。" if is_chinese_language(report_language) else ". "
            appendix.extend([f"![{image['caption']}]({image['path']})", "", f"*{image['caption']}{separator}{image['note']}*", ""])
        rendered = rendered.rstrip() + "\n" + "\n".join(appendix).strip() + "\n"
    return rendered


def clean_final_markdown(markdown: str) -> str:
    lines = []
    for line in markdown.splitlines():
        if INTERNAL_TERMS_RE.search(line):
            continue
        lines.append(line.rstrip())
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def ensure_standard_sections(markdown: str, report_language: str) -> str:
    if not is_chinese_language(report_language):
        if "## Core Takeaways" not in markdown:
            markdown = re.sub(r"^(# .+?\n)", r"\1\n## Core Takeaways\n\nThis report reorganizes the source video into a readable structure while preserving the main claims, examples, numbers, and caveats.\n\n", markdown, count=1, flags=re.DOTALL)
        if "## Reader Notes" not in markdown:
            markdown = markdown.rstrip() + "\n\n## Reader Notes\n\nTreat this report as an interpretation framework and review checklist, not as automated trading or decision instruction.\n"
        return markdown

    if "## 核心结论" not in markdown:
        markdown = re.sub(
            r"^(# .+?\n)",
            r"\1\n## 核心结论\n\n本报告按照视频原始内容重组为主题化结构，保留主要论点、案例、数字、限制条件和风险提醒。\n\n",
            markdown,
            count=1,
            flags=re.DOTALL,
        )
    if "## 读者使用提醒" not in markdown:
        markdown = markdown.rstrip() + "\n\n## 读者使用提醒\n\n以上内容来自视频本身的论证、案例和数字。实际使用时，应把它当作理解框架和风险检查清单，而不是自动交易或自动决策指令。\n"
    return markdown


def validate_final_report(markdown: str, image_bank: list[dict[str, str]], report_language: str) -> None:
    text = plain_text(markdown)
    errors: list[str] = []
    minimum_chars = 1200 if is_chinese_language(report_language) else 1800
    if len(text) < minimum_chars:
        errors.append(f"final report is too short: {len(text)} chars")
    if INTERNAL_TERMS_RE.search(markdown):
        errors.append("final report exposes internal pipeline terms")
    if re.search(r"\[IMAGE:img_\d+\]", markdown):
        errors.append("final report still contains unresolved image placeholders")
    if image_bank and "![" not in markdown:
        errors.append("final report has image candidates but embeds no images")
    errors.extend(language_compliance_errors(markdown, report_language))
    required_headings = (
        ("## 核心结论", "## 读者使用提醒")
        if is_chinese_language(report_language)
        else ("## Core Takeaways", "## Reader Notes")
    )
    for heading in required_headings:
        if heading not in markdown:
            errors.append(f"missing required heading: {heading}")
    if errors:
        raise RuntimeError("; ".join(errors))


def main() -> int:
    args = parse_args()
    report_path = Path(args.report_json).expanduser().resolve()
    manifest_path = Path(args.visual_manifest).expanduser().resolve()
    markdown_path = Path(args.markdown).expanduser().resolve()
    html_path = Path(args.html).expanduser().resolve()
    pdf_path = Path(args.pdf).expanduser().resolve() if args.pdf else None
    report = json.loads(report_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    report_language = infer_report_language(report, args.report_language)
    title = args.title or infer_report_title(report, manifest, report_language)
    image_bank = build_image_bank(manifest, markdown_path, args.max_images, report_language)

    if args.provider == "openai":
        try:
            markdown = compose_with_openai(
                report,
                image_bank,
                title,
                model=args.model,
                base_url=args.base_url,
                max_output_tokens=args.max_output_tokens,
                chunk_chars=args.llm_chunk_chars,
                transcript_limit=args.transcript_char_limit,
                report_language=report_language,
            )
            if len(plain_text(markdown)) < 4500:
                raise RuntimeError("text provider returned a report that is too short")
        except Exception as exc:
            print(f"warning: text provider failed; using deterministic final report: {exc}", file=sys.stderr)
            markdown = deterministic_report(report, image_bank, title, report_language)
    else:
        markdown = deterministic_report(report, image_bank, title, report_language)
    markdown = clean_final_markdown(ensure_standard_sections(render_images(markdown, image_bank, report_language), report_language))
    language_errors = language_compliance_errors(markdown, report_language)
    if language_errors and args.provider == "openai":
        print(
            "warning: final report language check failed; rewriting in requested language: "
            + "; ".join(language_errors),
            file=sys.stderr,
        )
        markdown = rewrite_report_language(
            markdown,
            report_language=report_language,
            model=args.model,
            base_url=args.base_url,
            max_output_tokens=args.max_output_tokens,
        )
        markdown = clean_final_markdown(
            ensure_standard_sections(render_images(markdown, image_bank, report_language), report_language)
        )
    validate_final_report(markdown, image_bank, report_language)

    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(markdown, encoding="utf-8")
    html_path.write_text(build_html(markdown, manifest, html_path, title), encoding="utf-8")
    result: dict[str, str | None] = {"markdown": str(markdown_path), "html": str(html_path), "pdf": None}
    if pdf_path:
        ok, message = write_pdf(html_path, pdf_path)
        result["pdf"] = str(pdf_path) if ok else None
        result["pdf_status"] = "done" if ok else "error"
        result["pdf_message"] = message
    result["generated_at"] = datetime.now(timezone.utc).isoformat()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
