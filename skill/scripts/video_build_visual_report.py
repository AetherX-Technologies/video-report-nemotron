#!/usr/bin/env python3
"""Build Markdown, HTML, and optional PDF visual reports from an OCR/vision manifest."""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a visual Markdown/HTML/PDF report from a manifest.")
    parser.add_argument("manifest", help="Manifest after frame capture/OCR.")
    parser.add_argument("--markdown", required=True, help="Markdown output path.")
    parser.add_argument("--html", required=True, help="HTML output path.")
    parser.add_argument("--pdf", default=None, help="Optional PDF output path.")
    parser.add_argument("--title", default=None, help="Override report title.")
    parser.add_argument(
        "--style",
        choices=("final", "evidence"),
        default="final",
        help="final builds a user-facing report; evidence keeps the full OCR/vision audit trail.",
    )
    parser.add_argument("--max-images", type=int, default=10, help="Maximum images in final style.")
    return parser.parse_args()


def relpath(path: str, base: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(base.resolve()))
    except ValueError:
        return str(Path(path).resolve())


def frame_caption(frame: dict) -> str:
    ocr = frame.get("ocr", {})
    if ocr.get("status") == "done":
        if ocr.get("adequate"):
            return "OCR 可用"
        return "OCR 内容不足，建议多模态复核"
    if ocr.get("status") == "missing_dependency":
        return "OCR 未执行：缺少依赖"
    if ocr.get("status") == "error":
        return "OCR 执行失败，建议多模态复核"
    return "OCR 未执行"


def vision_caption(frame: dict) -> str:
    vision = frame.get("vision", {})
    status = vision.get("status")
    if status == "done":
        return f"多模态已完成 ({vision.get('provider')}/{vision.get('model')})"
    if status == "error":
        return f"多模态失败：{vision.get('error', '')[:160]}"
    if status == "pending":
        return "多模态待处理"
    return "多模态未运行"


def plain_text(value: str) -> str:
    return " ".join(value.split()).strip()


def parse_timestamp_start(value: str | None) -> int:
    if not value:
        return 0
    start = value.split("-", 1)[0].strip()
    parts = [int(part) for part in start.split(":") if part.isdigit()]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0] if parts else 0


def segment_heading(segment: dict) -> str:
    start = parse_timestamp_start(segment.get("timestamp"))
    if start < 270:
        return "市场博弈框架"
    if start < 540:
        return "新闻语言与状态识别"
    if start < 720:
        return "典型操控案例"
    if start < 900:
        return "操控识别雷达"
    return "状态切换与策略窗口"


def inferred_segment_heading(segment: dict) -> str:
    transcript = plain_text(segment.get("transcript", ""))
    if not transcript:
        return str(segment.get("timestamp") or segment.get("id"))
    if "矩阵" in transcript or "博弈" in transcript:
        return "市场博弈框架"
    if "新闻" in transcript or "NLP" in transcript or "情绪" in transcript or "政策" in transcript:
        return "新闻语言与状态识别"
    if "操控" in transcript or "订单" in transcript or "交易所" in transcript or "刷量" in transcript:
        return "操控识别与交易异常"
    if "信号" in transcript or "切换" in transcript or "窗口" in transcript:
        return "状态切换与行动窗口"
    return "关键片段"


def segment_takeaway(segment: dict) -> str:
    heading = segment_heading(segment)
    timestamp = segment.get("timestamp")
    if heading == "市场博弈框架":
        return f"`{timestamp}` 建立市场状态框架：用博弈主体、系统总效用和可观测信号判断自己处在哪类市场环境。"
    if heading == "新闻语言与状态识别":
        return f"`{timestamp}` 讨论新闻/NLP 指标：情绪分、政策焦虑、信息新颖度等可作为状态识别证据。"
    if heading == "典型操控案例":
        return f"`{timestamp}` 用历史案例说明：订单簿层面的 spoofing、虚假挂单和流动性冲击会放大市场波动。"
    if heading == "操控识别雷达":
        return f"`{timestamp}` 总结散户可观察的操控信号：撤单/成交比例、整数价位集中、刷量痕迹、稳定币流入和跨所价差。"
    if heading == "状态切换与策略窗口":
        return f"`{timestamp}` 给出状态切换线索：结合 VIX、期权情绪、订单流毒性、成交量和热点传播窗口判断风险。"
    return f"`{timestamp}` 该片段提供补充证据。"


def section_summary(heading: str, segments: list[dict]) -> list[str]:
    if heading == "市场博弈框架":
        return [
            "视频先把市场拆成机构、做市商/高频、知情参与者与噪音散户等主体，再讨论不同状态下谁受益、谁承压。",
            "重点不是事后解释涨跌，而是用可观察变量判断所处市场状态，避免在高压或操控环境中被动承接流动性。",
        ]
    if heading == "新闻语言与状态识别":
        return [
            "这一部分强调“语言先动，价格后动”：新闻主题集中、情绪极端和政策不确定性上升，可能比价格指标更早暴露状态切换。",
            "这些指标适合用来识别状态，而不是直接替代交易决策；一旦某种新闻策略变得拥挤，优势会很快衰减。",
        ]
    if heading == "典型操控案例":
        return [
            "视频用 spoofing、闪电崩盘和大型机构贵金属操控案说明，微观订单簿行为可能触发宏观层面的流动性冲击。",
            "核心启发是：操控往往留下结构化痕迹，例如大单秒撤、虚假挂单比例异常、订单流毒性提前上升。",
        ]
    if heading == "操控识别雷达":
        return [
            "加密市场部分关注刷量、整数价位集中、稳定币流入、Pump & Dump 和跨交易所价差快速抹平等信号。",
            "这些信号不能单独构成买卖依据，但能帮助散户判断对手方是否可能是高频算法、交易所刷量或人为拉盘。",
        ]
    if heading == "状态切换与策略窗口":
        return [
            "结尾把策略收束到三类状态切换：动荡到绞杀、绞杀到衰退、衰退到静默。",
            "作者强调热点传播有时间窗口，越晚接触到共识叙事，越可能从参与者变成接盘者。",
        ]
    return [f"该部分包含 {len(segments)} 个需要视觉核对的片段。"]


def evidence_summary(evidence_types: list[str]) -> str:
    labels = {
        "matrix": "矩阵/状态框架",
        "chart": "曲线或指标图",
        "table": "表格或阈值清单",
        "dense_numbers": "密集数值",
        "orderbook": "订单簿/交易数据",
        "case": "案例材料",
    }
    rendered = [labels[item] for item in evidence_types if item in labels]
    return "、".join(rendered) if rendered else "视频画面证据"


def frame_visual_note(segment: dict, frame: dict) -> str:
    vision_text = plain_text(frame.get("vision", {}).get("text", ""))
    if vision_text and "不建议嵌入" not in vision_text:
        return vision_text[:220]
    evidence = evidence_summary(segment.get("evidence_types", []))
    ocr = frame.get("ocr", {})
    if ocr.get("adequate"):
        return f"该帧包含可读的{evidence}，适合作为本节视觉证据。"
    return f"该帧用于定位本节画面上下文；详细 OCR 和多模态结果保留在证据文件中。"


def score_frame(segment: dict, frame: dict) -> int:
    score = 0
    if segment.get("priority") == "high":
        score += 4
    elif segment.get("priority") == "medium":
        score += 2
    evidence_types = set(segment.get("evidence_types", []))
    score += 2 * len(evidence_types.intersection({"chart", "matrix", "table", "dense_numbers"}))
    ocr = frame.get("ocr", {})
    if ocr.get("adequate"):
        score += 3
    if len(ocr.get("text", "")) > 80:
        score += 2
    vision = frame.get("vision", {})
    if vision.get("status") == "done" and "不建议" not in vision.get("text", ""):
        score += 2
    return score


def select_final_frames(manifest: dict, max_images: int) -> list[tuple[dict, dict]]:
    selected: list[tuple[dict, dict]] = []
    for segment in manifest.get("segments", []):
        frames = segment.get("frames", [])
        if not segment.get("needs_video") or not frames:
            continue
        best = max(frames, key=lambda frame: score_frame(segment, frame))
        selected.append((segment, best))
    selected.sort(key=lambda item: (parse_timestamp_start(item[0].get("timestamp")), -score_frame(*item)))
    return selected[:max_images]


def build_final_markdown(manifest: dict, output_path: Path, title: str, max_images: int) -> str:
    base = output_path.parent
    visual_segments = [segment for segment in manifest.get("segments", []) if segment.get("needs_video")]
    key_frames = select_final_frames(manifest, max_images)
    lines = [
        f"# {title}",
        "",
        "## 核心结论",
        "",
        "视频的主线是：市场状态不是靠事后涨跌解释出来的，而应通过可观察变量提前识别。支付矩阵、新闻语言、订单流、成交结构和情绪指标共同构成一套风险识别框架。最终目标不是预测每一次价格方向，而是判断自己是否处在容易被流动性收割、操控放大或情绪滞后的环境中。",
        "",
        "## 章节整理",
        "",
    ]

    grouped: dict[str, list[dict]] = {}
    for segment in visual_segments:
        grouped.setdefault(segment_heading(segment), []).append(segment)
    for heading, segments in grouped.items():
        lines.extend([f"### {heading}", ""])
        lines.extend(f"- {item}" for item in section_summary(heading, segments))
        lines.append("")
        lines.append("涉及片段：")
        for segment in segments[:5]:
            lines.append(f"- {segment_takeaway(segment)}")
        lines.append("")

    if key_frames:
        lines.extend(["## 关键图像证据", ""])
    for index, (segment, frame) in enumerate(key_frames, start=1):
        frame_path = relpath(frame["path"], base)
        lines.extend(
            [
                f"### 图 {index}. {segment_heading(segment)} ({frame.get('timestamp')})",
                "",
                f"![{segment.get('id')} {frame.get('timestamp')}]({frame_path})",
                "",
                f"- 对应片段：`{segment.get('timestamp')}`",
                f"- 画面用途：展示{evidence_summary(segment.get('evidence_types', []))}",
            ]
        )
        lines.append(f"- 视觉解读：{frame_visual_note(segment, frame)}")
        lines.append("")

    lines.extend(
        [
            "## 方法说明",
            "",
            f"- 原视频：{manifest.get('source')}",
            f"- 视觉片段：{len(visual_segments)} 个时间块",
            f"- 报告插图：{len(key_frames)} 张关键帧",
            "- 处理顺序：按转写时间块判断是否需要看视频，再取帧；OCR 先行，OCR 不足时才调用多模态。",
            "",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def build_markdown(manifest: dict, output_path: Path, title: str) -> str:
    base = output_path.parent
    lines = [
        f"# {title}",
        "",
        f"- Source: {manifest.get('source')}",
        f"- Generated: {datetime.now(timezone.utc).isoformat()}",
        f"- Policy: block-level visual manifest, OCR first, multimodal only when OCR is weak.",
        "",
        "## Visual Segments",
        "",
    ]
    for segment in manifest.get("segments", []):
        if not segment.get("needs_video"):
            continue
        lines.extend(
            [
                f"### {segment.get('timestamp')} {segment.get('id')}",
                "",
                f"- Priority: {segment.get('priority')}",
                f"- Rationale: {segment.get('rationale')}",
                f"- Evidence types: {', '.join(segment.get('evidence_types', [])) or 'none'}",
                "",
                "**Transcript block**",
                "",
                segment.get("transcript", ""),
                "",
            ]
        )
        if segment.get("visual_questions"):
            lines.append("**Visual questions**")
            lines.append("")
            lines.extend(f"- {question}" for question in segment.get("visual_questions", []))
            lines.append("")
        for frame in segment.get("frames", []):
            frame_path = relpath(frame["path"], base)
            lines.extend(
                [
                    f"![{segment.get('id')} {frame.get('timestamp')}]({frame_path})",
                    "",
                    f"- Frame time: {frame.get('timestamp')}",
                    f"- OCR status: {frame_caption(frame)}",
                ]
            )
            ocr_text = frame.get("ocr", {}).get("text", "").strip()
            if ocr_text:
                lines.extend(["", "**OCR text**", "", "```text", ocr_text[:3000], "```"])
            vision_text = frame.get("vision", {}).get("text", "").strip()
            if vision_text:
                lines.extend(["", "**Multimodal analysis**", "", vision_text])
            elif frame.get("ocr", {}).get("needs_multimodal"):
                lines.append(f"- Multimodal fallback: {vision_caption(frame)}")
            lines.append("")
    return "\n".join(lines).strip() + "\n"


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_IMAGE_RE = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$")
_ORDERED_RE = re.compile(r"^\s*\d+[.)]\s+(.+)$")
_UNORDERED_RE = re.compile(r"^\s*[-*]\s+(.+)$")
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")


def render_inline_markdown(text: str) -> str:
    """Render the inline Markdown used by generated reports."""
    code_spans: list[str] = []

    def keep_code(match: re.Match[str]) -> str:
        code_spans.append(f"<code>{html.escape(match.group(1))}</code>")
        return f"\x00CODE{len(code_spans) - 1}\x00"

    rendered = re.sub(r"`([^`]+)`", keep_code, text)
    rendered = html.escape(rendered)
    rendered = re.sub(
        r"(?<!!)\[([^\]]+)\]\(([^)\s]+)(?:\s+&quot;[^&]*&quot;)?\)",
        r'<a href="\2">\1</a>',
        rendered,
    )
    rendered = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", rendered)
    rendered = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", rendered)
    for index, code in enumerate(code_spans):
        rendered = rendered.replace(f"\x00CODE{index}\x00", code)
    return rendered


def parse_table_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def table_alignment(separator_cell: str) -> str | None:
    cell = separator_cell.strip()
    if cell.startswith(":") and cell.endswith(":"):
        return "center"
    if cell.endswith(":"):
        return "right"
    if cell.startswith(":"):
        return "left"
    return None


def is_italic_caption(line: str) -> bool:
    stripped = line.strip()
    return len(stripped) > 2 and stripped.startswith("*") and stripped.endswith("*") and not stripped.startswith("**")


def render_table(lines: list[str], start: int) -> tuple[str, int]:
    header = parse_table_row(lines[start])
    separators = parse_table_row(lines[start + 1])
    alignments = [table_alignment(cell) for cell in separators]
    rows: list[list[str]] = []
    index = start + 2
    while index < len(lines):
        line = lines[index].rstrip()
        if not line.strip() or "|" not in line:
            break
        rows.append(parse_table_row(line))
        index += 1

    parts = ["<table>", "<thead>", "<tr>"]
    for column, cell in enumerate(header):
        align = f' style="text-align: {alignments[column]}"' if column < len(alignments) and alignments[column] else ""
        parts.append(f"<th{align}>{render_inline_markdown(cell)}</th>")
    parts.extend(["</tr>", "</thead>", "<tbody>"])
    for row in rows:
        parts.append("<tr>")
        for column, cell in enumerate(row):
            align = f' style="text-align: {alignments[column]}"' if column < len(alignments) and alignments[column] else ""
            parts.append(f"<td{align}>{render_inline_markdown(cell)}</td>")
        parts.append("</tr>")
    parts.extend(["</tbody>", "</table>"])
    return "\n".join(parts), index


def split_quoted_paragraphs(lines: list[str]) -> list[list[str]]:
    paragraphs: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.strip():
            current.append(line.strip())
        elif current:
            paragraphs.append(current)
            current = []
    if current:
        paragraphs.append(current)
    return paragraphs


def render_blockquote(lines: list[str], start: int) -> tuple[str, int]:
    quoted: list[str] = []
    index = start
    while index < len(lines):
        line = lines[index].rstrip()
        if not line.startswith(">"):
            break
        quoted.append(line[1:].lstrip())
        index += 1
    paragraphs = []
    for paragraph in split_quoted_paragraphs(quoted):
        paragraphs.append(f"<p>{'<br>'.join(render_inline_markdown(item) for item in paragraph)}</p>")
    return "<blockquote>\n" + "\n".join(paragraphs) + "\n</blockquote>", index


def render_list(lines: list[str], start: int, ordered: bool) -> tuple[str, int]:
    pattern = _ORDERED_RE if ordered else _UNORDERED_RE
    tag = "ol" if ordered else "ul"
    items: list[list[str]] = []
    index = start
    while index < len(lines):
        match = pattern.match(lines[index])
        if not match:
            break
        parts = [match.group(1).strip()]
        index += 1
        while index < len(lines):
            line = lines[index]
            if not line.strip():
                next_index = index + 1
                while next_index < len(lines) and not lines[next_index].strip():
                    next_index += 1
                if next_index < len(lines) and pattern.match(lines[next_index]):
                    index = next_index
                    break
                index = next_index
                break
            if pattern.match(line) or _HEADING_RE.match(line) or line.startswith(">"):
                break
            if line.startswith((" ", "\t")):
                parts.append(line.strip())
                index += 1
                continue
            break
        items.append(parts)
        if index >= len(lines) or not pattern.match(lines[index]):
            break

    rendered_items = []
    for item in items:
        first, *details = item
        content = render_inline_markdown(first)
        if details:
            content += "".join(f"<p>{render_inline_markdown(detail)}</p>" for detail in details)
        rendered_items.append(f"<li>{content}</li>")
    return f"<{tag}>\n" + "\n".join(rendered_items) + f"\n</{tag}>", index


def render_paragraph(lines: list[str], start: int) -> tuple[str, int]:
    paragraph: list[str] = []
    index = start
    while index < len(lines):
        line = lines[index].rstrip()
        next_is_table = index + 1 < len(lines) and _TABLE_SEPARATOR_RE.match(lines[index + 1].rstrip())
        if (
            not line.strip()
            or line.startswith("```")
            or _HEADING_RE.match(line)
            or _IMAGE_RE.match(line)
            or line.startswith(">")
            or _ORDERED_RE.match(line)
            or _UNORDERED_RE.match(line)
            or next_is_table
        ):
            break
        paragraph.append(line.strip())
        index += 1
    return f"<p>{render_inline_markdown(' '.join(paragraph))}</p>", index


def build_html(markdown_text: str, manifest: dict, output_path: Path, title: str) -> str:
    """Render the Markdown subset produced by this skill into print-ready HTML."""
    lines = markdown_text.splitlines()
    html_lines: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index].rstrip()
        if not line.strip():
            index += 1
            continue
        if line.startswith("```"):
            language = line[3:].strip()
            code_lines: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].startswith("```"):
                code_lines.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            class_name = f' class="language-{html.escape(language, quote=True)}"' if language else ""
            html_lines.append(f"<pre><code{class_name}>{html.escape(chr(10).join(code_lines))}</code></pre>")
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            level = min(len(heading.group(1)), 6)
            html_lines.append(f"<h{level}>{render_inline_markdown(heading.group(2))}</h{level}>")
            index += 1
            continue
        image = _IMAGE_RE.match(line)
        if image:
            alt = image.group(1)
            src = image.group(2)
            caption = ""
            next_index = index + 1
            if next_index < len(lines) and not lines[next_index].strip():
                next_index += 1
            if next_index < len(lines) and is_italic_caption(lines[next_index]):
                caption = lines[next_index].strip()[1:-1]
                index = next_index + 1
            else:
                index += 1
            figure = [
                "<figure>",
                f'<img src="{html.escape(src, quote=True)}" alt="{html.escape(alt, quote=True)}">',
            ]
            if caption:
                figure.append(f"<figcaption>{render_inline_markdown(caption)}</figcaption>")
            figure.append("</figure>")
            html_lines.append("\n".join(figure))
            continue
        if line.startswith(">"):
            rendered, index = render_blockquote(lines, index)
            html_lines.append(rendered)
            continue
        if index + 1 < len(lines) and "|" in line and _TABLE_SEPARATOR_RE.match(lines[index + 1].rstrip()):
            rendered, index = render_table(lines, index)
            html_lines.append(rendered)
            continue
        if _ORDERED_RE.match(line):
            rendered, index = render_list(lines, index, ordered=True)
            html_lines.append(rendered)
            continue
        if _UNORDERED_RE.match(line):
            rendered, index = render_list(lines, index, ordered=False)
            html_lines.append(rendered)
            continue
        rendered, index = render_paragraph(lines, index)
        html_lines.append(rendered)

    document = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
:root {{ color-scheme: light; }}
* {{ box-sizing: border-box; }}
body {{
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
  max-width: 900px;
  margin: 44px auto;
  padding: 0 28px;
  line-height: 1.68;
  color: #171717;
  background: #fff;
  font-size: 16px;
}}
h1, h2, h3, h4, h5, h6 {{ page-break-after: avoid; break-after: avoid; line-height: 1.28; }}
h1 {{ font-size: 30px; margin: 0 0 22px; letter-spacing: 0; }}
h2 {{ font-size: 22px; margin: 34px 0 14px; border-bottom: 1px solid #d8dee4; padding-bottom: 8px; }}
h3 {{ font-size: 18px; margin: 26px 0 10px; }}
p {{ margin: 10px 0; }}
a {{ color: #0b57d0; text-decoration: none; }}
strong {{ font-weight: 700; }}
ol, ul {{ margin: 10px 0 16px 1.35em; padding: 0; }}
li {{ margin: 6px 0; padding-left: 2px; }}
li p {{ margin: 6px 0 0; }}
blockquote {{
  margin: 16px 0;
  padding: 10px 16px;
  border-left: 4px solid #64748b;
  background: #f8fafc;
  color: #334155;
  page-break-inside: avoid;
}}
blockquote p {{ margin: 4px 0; }}
table {{
  width: 100%;
  border-collapse: collapse;
  margin: 16px 0 22px;
  font-size: 14px;
  page-break-inside: avoid;
}}
th, td {{ border: 1px solid #d0d7de; padding: 7px 9px; vertical-align: top; }}
th {{ background: #f6f8fa; font-weight: 700; }}
tr:nth-child(even) td {{ background: #fbfbfc; }}
img {{ display: block; max-width: 100%; height: auto; border: 1px solid #d8dee4; }}
figure {{ margin: 20px 0 24px; page-break-inside: avoid; break-inside: avoid; }}
figcaption {{ margin-top: 7px; color: #57606a; font-size: 13px; line-height: 1.5; }}
pre {{
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  background: #f6f8fa;
  padding: 12px;
  border: 1px solid #d8dee4;
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.45;
}}
code {{ background: #f6f8fa; padding: 1px 4px; border-radius: 4px; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
pre code {{ background: transparent; padding: 0; }}
@page {{ size: A4; margin: 18mm 16mm; }}
@media print {{
  body {{ max-width: none; margin: 0; padding: 0; font-size: 12.5pt; }}
  h1 {{ font-size: 24pt; }}
  h2 {{ font-size: 17pt; }}
  h3 {{ font-size: 14pt; }}
  table {{ font-size: 10.5pt; }}
  figcaption {{ font-size: 10pt; }}
}}
</style>
</head>
<body>
{chr(10).join(html_lines)}
</body>
</html>
"""
    return document


def write_pdf(html_path: Path, pdf_path: Path) -> tuple[bool, str]:
    playwright = shutil.which("playwright")
    if not playwright:
        return False, "playwright command not found"
    process = subprocess.run(
        [playwright, "pdf", html_path.resolve().as_uri(), str(pdf_path)],
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
    manifest_path = Path(args.manifest).expanduser().resolve()
    markdown_path = Path(args.markdown).expanduser().resolve()
    html_path = Path(args.html).expanduser().resolve()
    pdf_path = Path(args.pdf).expanduser().resolve() if args.pdf else None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    title = args.title or f"Visual Report: {manifest.get('title') or 'video'}"

    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    if args.style == "evidence":
        markdown_text = build_markdown(manifest, markdown_path, title)
    else:
        markdown_text = build_final_markdown(manifest, markdown_path, title, args.max_images)
    markdown_path.write_text(markdown_text, encoding="utf-8")
    html_path.write_text(build_html(markdown_text, manifest, html_path, title), encoding="utf-8")

    result = {"markdown": str(markdown_path), "html": str(html_path), "pdf": None}
    if pdf_path:
        ok, message = write_pdf(html_path, pdf_path)
        result["pdf"] = str(pdf_path) if ok else None
        result["pdf_status"] = "done" if ok else "error"
        result["pdf_message"] = message

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
