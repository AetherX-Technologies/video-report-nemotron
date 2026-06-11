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
from video_multimodal_frames import DEFAULT_BASE_URL, DEFAULT_MODEL, extract_response_text, load_env_from_argv


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


def infer_report_title(report: dict[str, Any], manifest: dict[str, Any]) -> str:
    candidate = str(report.get("title") or manifest.get("title") or "").strip()
    if candidate and not re.fullmatch(r"[A-Za-z0-9_-]{6,}", candidate):
        return f"{candidate} 深度报告"
    source = str(report.get("source") or manifest.get("source") or "").strip()
    if source:
        return "视频内容深度报告"
    return "内容深度报告"


def clean_visual_note(note: str, evidence_types: list[str]) -> str:
    text = plain_text(note)
    if not text or "不建议嵌入" in text:
        text = f"画面包含{evidence_summary(evidence_types)}。"
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
    return (text or f"画面包含{evidence_summary(evidence_types)}。")[:240]


def image_caption(segment: dict[str, Any], frame: dict[str, Any], index: int) -> str:
    evidence = evidence_summary(list(segment.get("evidence_types", [])))
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


def build_image_bank(manifest: dict[str, Any], markdown_path: Path, max_images: int) -> list[dict[str, str]]:
    bank: list[dict[str, str]] = []
    for index, (segment, frame) in enumerate(select_report_frames(manifest, max_images), start=1):
        evidence_types = list(segment.get("evidence_types", []))
        bank.append(
            {
                "id": f"img_{index:02d}",
                "path": relpath(frame["path"], markdown_path.parent),
                "caption": image_caption(segment, frame, index),
                "note": clean_visual_note(frame_visual_note(segment, frame), evidence_types),
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


def build_prompt(report: dict[str, Any], manifest: dict[str, Any], image_bank: list[dict[str, str]], title: str, limit: int) -> str:
    transcript = str(report.get("transcript", ""))[:limit]
    source = report.get("source") or manifest.get("source")
    return f"""你是一名中文深度报告作者。请把下面的视频转写整理成一份可直接交给用户阅读的完整图文报告。

硬性要求：
- 报告必须比原视频更清晰，但不能少掉原视频里的实质信息、例子、数字、阈值、案例、限定条件和提醒。
- 可以去掉口头禅、重复语、寒暄、订阅引导和明显 ASR 错字，但不要把细节压缩成几条概括。
- 不要按时间轴逐段复述；要按主题重组。
- 不要出现内部流程词或编辑说明，例如 manifest、OCR、ASR、转写块、证据目录、视觉问题、需要配图、适合插入、这一段配图。
- 图片必须自然嵌入正文：只使用给定占位符 [IMAGE:img_XX]，不要编造图片。图片前后写的是内容解读，不是“为什么这里要配图”。
- 所有图文说明都要服务于视频内容本身。
- 用中文 Markdown 输出，不构成投资建议。

报告标题：{title}
原视频：{source}

可用图片：
{image_bank_prompt(image_bank)}

视频转写内容：
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


def chunk_prompt(title: str, source: str, chunk: list[TranscriptSegment], chunk_index: int, chunk_count: int) -> str:
    transcript = "\n\n".join(
        f"[{item.timestamp}] {item.text}" if item.timestamp else item.text
        for item in chunk
    )
    return f"""你是一名中文深度报告作者。下面是同一个视频的第 {chunk_index}/{chunk_count} 部分转写。

请把这一部分整理成最终报告中的一个或多个章节，要求：
- 保留这一部分里所有实质信息：论点、例子、数字、比例、阈值、案例名称、时间、限制条件和提醒。
- 只能删口头禅、重复语、订阅引导和明显无意义的 ASR 噪声；不要删细节。
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
) -> str:
    transcript = str(report.get("transcript", ""))[:transcript_limit]
    segments = parse_segments(transcript)
    if not segments:
        raise RuntimeError("Transcript is empty.")
    chunks = chunk_segments(segments, chunk_chars)
    source = str(report.get("source") or "")
    sections: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        prompt = chunk_prompt(title, source, chunk, index, len(chunks))
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
        f"> 原视频：{source}  ",
        "> 说明：本报告按主题重组视频内容，保留主要细节、数字、案例和提醒；不构成投资建议。",
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
            lines.extend(render_image_placeholder(image))
    lines.extend(
        [
            "## 使用提醒",
            "",
            "以上内容整理自视频本身。涉及市场、投资、风险或决策时，应结合原始资料、数据源和个人约束复核；本报告不构成投资建议。",
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
        for sentence in re.split(r"(?<=[。！？!?])\s*", compress_asr_text(segment.text)):
            sentence = plain_text(sentence)
            if not sentence:
                continue
            if any(marker in sentence for marker in ("结论", "核心", "关键", "本质", "问题", "意味着", "记住", "真正", "不是", "而是")):
                claims.append(sentence)
            if len(claims) >= limit:
                return claims
    return claims


def nearest_image(images: list[ImageItem], used: set[str], start: int) -> ImageItem | None:
    candidates = [image for image in images if image.id not in used]
    if not candidates:
        return None
    image = min(candidates, key=lambda item: abs(item.start - start))
    used.add(image.id)
    return image


def render_image_placeholder(image: ImageItem) -> list[str]:
    return [f"[IMAGE:{image.id}]", "", f"*{image.caption}。{image.note}*", ""]


def deterministic_report(report: dict[str, Any], image_bank: list[dict[str, str]], title: str) -> str:
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
        f"> 原视频：{source}  ",
        "> 说明：本报告按主题重组视频内容，保留主要细节、数字、案例和提醒；不构成投资建议。",
        "",
        "## 核心结论",
        "",
        build_opening_summary(segments),
        "",
    ]
    if images:
        first_image = nearest_image(images, used_images, segments[0].start if segments else 0)
        if first_image:
            lines.extend(render_image_placeholder(first_image))

    claims = extract_core_claims(segments)
    if claims:
        lines.extend(["## 关键论点", ""])
        for claim in claims:
            lines.extend([f"- {claim}", ""])

    details = extract_detail_items(" ".join(segment.text for segment in segments))
    if details:
        lines.extend(["## 重要数字、案例与限定条件", ""])
        for detail in details:
            lines.extend([f"- {detail}", ""])

    groups = group_segments(segments)
    for index, (heading, items) in enumerate(groups, start=1):
        lines.extend([f"## {index}. {heading}", ""])
        body = " ".join(item.text for item in items)
        for paragraph in paragraphize(body):
            lines.extend([paragraph, ""])
        group_start = items[0].start if items else 0
        image = nearest_image(images, used_images, group_start)
        if image:
            lines.extend(render_image_placeholder(image))

    lines.extend(
        [
            "## 读者使用提醒",
            "",
            "以上内容来自视频本身的论证、案例和数字。实际使用时，应把它当作理解框架和风险检查清单，而不是自动交易或自动决策指令。",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def render_images(markdown: str, image_bank: list[dict[str, str]]) -> str:
    rendered = markdown
    used: set[str] = set()
    for image in image_bank:
        placeholder = f"[IMAGE:{image['id']}]"
        figure = f"![{image['caption']}]({image['path']})"
        if placeholder in rendered:
            rendered = rendered.replace(placeholder, figure)
            used.add(image["id"])
    if image_bank and not used:
        appendix = ["", "## 图像补充", ""]
        for image in image_bank[:6]:
            appendix.extend([f"![{image['caption']}]({image['path']})", "", f"*{image['caption']}。{image['note']}*", ""])
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


def main() -> int:
    args = parse_args()
    report_path = Path(args.report_json).expanduser().resolve()
    manifest_path = Path(args.visual_manifest).expanduser().resolve()
    markdown_path = Path(args.markdown).expanduser().resolve()
    html_path = Path(args.html).expanduser().resolve()
    pdf_path = Path(args.pdf).expanduser().resolve() if args.pdf else None
    report = json.loads(report_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    title = args.title or infer_report_title(report, manifest)
    image_bank = build_image_bank(manifest, markdown_path, args.max_images)

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
            )
            if len(plain_text(markdown)) < 4500:
                raise RuntimeError("text provider returned a report that is too short")
        except Exception as exc:
            print(f"warning: text provider failed; using deterministic final report: {exc}", file=sys.stderr)
            markdown = deterministic_report(report, image_bank, title)
    else:
        markdown = deterministic_report(report, image_bank, title)
    markdown = clean_final_markdown(render_images(markdown, image_bank))

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
