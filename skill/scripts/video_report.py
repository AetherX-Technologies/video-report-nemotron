#!/usr/bin/env python3
"""Create transcript and report artifacts from a video URL or local media file."""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


DEFAULT_MODEL = "mlx-community/nemotron-3.5-asr-streaming-0.6b-8bit"
SUPPORTED_FORMATS = ("markdown", "json", "both")
TRANSCRIPT_SOURCES = ("auto", "subtitles", "asr")


@dataclass
class ArtifactPaths:
    output_dir: str
    markdown: str | None
    json: str | None
    audio: str | None


@dataclass
class ProcessingResult:
    source: str
    title: str
    model: str
    language: str | None
    created_at: str
    transcript: str
    summary: str | None
    key_points: list[str]
    action_items: list[str]
    artifacts: ArtifactPaths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Get subtitles when available, otherwise transcribe with MLX Nemotron ASR, and write report artifacts."
    )
    parser.add_argument("source", help="Video/audio URL or local media file path.")
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"MLX ASR model repository. Default: {DEFAULT_MODEL}",
    )
    parser.add_argument(
        "--language",
        default=None,
        help="Optional Nemotron language prompt, for example en-US or zh-CN.",
    )
    parser.add_argument(
        "--title",
        default=None,
        help="Optional report title. Defaults to downloaded title or input filename.",
    )
    parser.add_argument(
        "--output-dir",
        default="video-report-output",
        help="Directory for report artifacts.",
    )
    parser.add_argument(
        "--format",
        choices=SUPPORTED_FORMATS,
        default="both",
        help="Artifact format to write.",
    )
    parser.add_argument(
        "--skip-summary",
        action="store_true",
        help="Only write transcript and metadata; skip heuristic report sections.",
    )
    parser.add_argument(
        "--transcript-source",
        choices=TRANSCRIPT_SOURCES,
        default="auto",
        help="auto prefers URL subtitles and falls back to ASR; subtitles requires subtitles; asr always uses local ASR.",
    )
    parser.add_argument(
        "--subtitle-languages",
        default=None,
        help="Comma-separated yt-dlp subtitle language selector. Defaults from --language, preferring zh/en.",
    )
    parser.add_argument(
        "--keep-workdir",
        action="store_true",
        help="Keep downloaded and normalized audio files in the output directory.",
    )
    parser.add_argument(
        "--att-context-size",
        default=None,
        help="Optional Nemotron look-ahead as LEFT,RIGHT, for example 56,13.",
    )
    parser.add_argument(
        "--chunk-seconds",
        type=int,
        default=90,
        help="Split audio into chunks of this many seconds before ASR. Use 0 to disable chunking.",
    )
    return parser.parse_args()


def is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def require_command(name: str, install_hint: str) -> None:
    if shutil.which(name) is None:
        raise SystemExit(f"Missing required command: {name}. {install_hint}")


def run_command(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.returncode != 0:
        rendered = " ".join(command)
        raise RuntimeError(
            f"Command failed ({process.returncode}): {rendered}\n"
            f"stdout:\n{process.stdout}\n"
            f"stderr:\n{process.stderr}"
        )
    return process


def run_optional_command(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def safe_slug(value: str, fallback: str = "video-report") -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-._")
    return normalized[:90] or fallback


def source_title(source: str, explicit_title: str | None) -> str:
    if explicit_title:
        return explicit_title
    if is_url(source):
        parsed = urlparse(source)
        path_name = parsed.path.rstrip("/").split("/")[-1]
        return " - ".join(part for part in (parsed.netloc, path_name) if part)
    return Path(source).stem


def download_audio(url: str, workdir: Path) -> Path:
    require_command("yt-dlp", "Install it with: pip install yt-dlp")
    output_template = str(workdir / "source.%(ext)s")
    command = [
        "yt-dlp",
        "--no-playlist",
        "--extract-audio",
        "--audio-format",
        "wav",
        "--audio-quality",
        "0",
        "--output",
        output_template,
        url,
    ]
    run_command(command)
    candidates = sorted(workdir.glob("source.*"))
    if not candidates:
        raise RuntimeError("yt-dlp completed but no audio file was created.")
    return candidates[0]


def default_subtitle_languages(language: str | None) -> str:
    if language:
        normalized = language.replace("_", "-")
        prefix = normalized.split("-")[0].lower()
        if prefix == "zh":
            return "zh.*,zh-Hans,zh-Hant,zh-CN,zh-TW,en.*"
        return f"{normalized},{prefix}.*,en.*,zh.*"
    return "zh.*,zh-Hans,zh-Hant,zh-CN,zh-TW,en.*"


def parse_vtt_timestamp(value: str) -> float:
    value = value.strip().replace(",", ".")
    parts = value.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
    elif len(parts) == 2:
        hours = "0"
        minutes, seconds = parts
    else:
        raise ValueError(f"Unsupported VTT timestamp: {value}")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def clean_caption_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def parse_vtt_cues(path: Path) -> list[tuple[float, float, str]]:
    cues: list[tuple[float, float, str]] = []
    current_start: float | None = None
    current_end: float | None = None
    current_lines: list[str] = []
    timestamp_re = re.compile(r"(?P<start>\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3})\s+-->\s+(?P<end>\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3})")

    def flush() -> None:
        nonlocal current_start, current_end, current_lines
        if current_start is None or current_end is None:
            current_lines = []
            return
        text = clean_caption_text(" ".join(current_lines))
        if text:
            cues.append((current_start, current_end, text))
        current_start = None
        current_end = None
        current_lines = []

    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line:
            flush()
            continue
        if line in {"WEBVTT", "STYLE"} or line.startswith(("NOTE", "Kind:", "Language:")):
            continue
        match = timestamp_re.search(line)
        if match:
            flush()
            current_start = parse_vtt_timestamp(match.group("start"))
            current_end = parse_vtt_timestamp(match.group("end"))
            continue
        if current_start is not None:
            current_lines.append(line)
    flush()
    return cues


def subtitle_priority(path: Path, language: str | None) -> tuple[int, str]:
    name = path.name.lower()
    preferred = [part.lower() for part in default_subtitle_languages(language).replace("*", "").split(",")]
    score = 100
    for index, item in enumerate(preferred):
        if item and item in name:
            score = index
            break
    return (score, name)


def cues_to_timestamped_transcript(cues: list[tuple[float, float, str]], block_seconds: int) -> str:
    if not cues:
        return ""
    block_size = block_seconds if block_seconds > 0 else 90
    blocks: list[tuple[float, float, list[str]]] = []
    current_start = (int(cues[0][0]) // block_size) * block_size
    current_end = current_start + block_size
    current_text: list[str] = []
    last_text = ""
    for start, end, text in cues:
        while start >= current_end and current_text:
            blocks.append((current_start, current_end, current_text))
            current_start = current_end
            current_end = current_start + block_size
            current_text = []
            last_text = ""
        if text != last_text:
            current_text.append(text)
            last_text = text
        current_end = max(current_end, min(current_start + block_size, end))
    if current_text:
        blocks.append((current_start, current_end, current_text))
    return "\n\n".join(
        f"[{format_timestamp(start)}-{format_timestamp(end)}] {' '.join(texts)}"
        for start, end, texts in blocks
    ).strip()


def fetch_url_subtitles(
    url: str,
    workdir: Path,
    *,
    language: str | None,
    subtitle_languages: str | None,
    block_seconds: int,
) -> str | None:
    require_command("yt-dlp", "Install it with: pip install yt-dlp")
    subtitles_dir = workdir / "subtitles"
    subtitles_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(subtitles_dir / "captions.%(ext)s")
    command = [
        "yt-dlp",
        "--skip-download",
        "--no-playlist",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs",
        subtitle_languages or default_subtitle_languages(language),
        "--sub-format",
        "vtt/best",
        "--output",
        output_template,
        url,
    ]
    process = run_optional_command(command)
    caption_files = sorted(subtitles_dir.glob("*.vtt"), key=lambda item: subtitle_priority(item, language))
    if process.returncode != 0 and not caption_files:
        return None
    for caption_file in caption_files:
        transcript = cues_to_timestamped_transcript(parse_vtt_cues(caption_file), block_seconds)
        if transcript:
            return transcript
    return None


def normalize_audio(input_path: Path, workdir: Path) -> Path:
    require_command("ffmpeg", "Install it with: brew install ffmpeg")
    output_path = workdir / "normalized.wav"
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-sample_fmt",
        "s16",
        str(output_path),
    ]
    run_command(command)
    return output_path


def audio_duration_seconds(input_path: Path) -> float:
    require_command("ffprobe", "Install ffmpeg with: brew install ffmpeg")
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(input_path),
    ]
    process = run_command(command)
    try:
        return float(process.stdout.strip())
    except ValueError as exc:
        raise RuntimeError(f"Could not read audio duration from ffprobe output: {process.stdout}") from exc


def format_timestamp(seconds: float) -> str:
    rounded = max(0, int(round(seconds)))
    hours, remainder = divmod(rounded, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def split_audio(input_path: Path, workdir: Path, chunk_seconds: int) -> list[tuple[Path, float, float]]:
    duration = audio_duration_seconds(input_path)
    if chunk_seconds <= 0 or duration <= chunk_seconds:
        return [(input_path, 0.0, duration)]

    chunks_dir = workdir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    chunks: list[tuple[Path, float, float]] = []
    start = 0.0
    index = 0
    while start < duration:
        end = min(duration, start + chunk_seconds)
        output_path = chunks_dir / f"chunk_{index:04d}.wav"
        command = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{end - start:.3f}",
            "-i",
            str(input_path),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-sample_fmt",
            "s16",
            str(output_path),
        ]
        run_command(command)
        chunks.append((output_path, start, end))
        start = end
        index += 1
    return chunks


def parse_att_context_size(value: str | None) -> list[int] | None:
    if not value:
        return None
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 2:
        raise SystemExit("--att-context-size must use LEFT,RIGHT format, for example 56,13.")
    try:
        return [int(parts[0]), int(parts[1])]
    except ValueError as exc:
        raise SystemExit("--att-context-size values must be integers.") from exc


def transcribe_with_python_api(
    audio_path: Path,
    *,
    model_name: str,
    language: str | None,
    att_context_size: list[int] | None,
) -> str:
    try:
        from mlx_audio.stt import load  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "Could not import mlx_audio.stt.load. Install Nemotron-capable mlx-audio with: "
            'pip install "git+https://github.com/Blaizzy/mlx-audio.git"'
        ) from exc

    model = load(model_name)
    kwargs: dict[str, Any] = {}
    if language:
        kwargs["language"] = language
    if att_context_size:
        kwargs["att_context_size"] = att_context_size
    result = model.generate(str(audio_path), **kwargs)
    text = getattr(result, "text", None)
    if text is None:
        if isinstance(result, dict):
            text = result.get("text")
        else:
            text = str(result)
    return str(text).strip()


def transcribe_with_cli(audio_path: Path, *, model_name: str) -> str:
    command = [
        sys.executable,
        "-m",
        "mlx_audio.stt.generate",
        "--model",
        model_name,
        "--audio",
        str(audio_path),
        "--format",
        "txt",
    ]
    process = run_command(command)
    return process.stdout.strip()


def transcribe_audio(
    audio_path: Path,
    *,
    model_name: str,
    language: str | None,
    att_context_size: list[int] | None,
    chunk_seconds: int,
) -> str:
    chunks = split_audio(audio_path, audio_path.parent, chunk_seconds)
    transcripts: list[str] = []
    for index, (chunk_path, start, end) in enumerate(chunks, start=1):
        print(
            f"Transcribing chunk {index}/{len(chunks)} "
            f"[{format_timestamp(start)}-{format_timestamp(end)}]",
            file=sys.stderr,
            flush=True,
        )
        try:
            chunk_transcript = transcribe_with_python_api(
                chunk_path,
                model_name=model_name,
                language=language,
                att_context_size=att_context_size,
            )
        except RuntimeError as api_error:
            if language or att_context_size:
                raise
            try:
                chunk_transcript = transcribe_with_cli(chunk_path, model_name=model_name)
            except Exception as cli_error:
                raise RuntimeError(f"{api_error}\nCLI fallback also failed: {cli_error}") from cli_error
        if chunk_transcript:
            if len(chunks) == 1:
                transcripts.append(chunk_transcript)
            else:
                transcripts.append(
                    f"[{format_timestamp(start)}-{format_timestamp(end)}] {chunk_transcript}"
                )

    transcript = "\n\n".join(transcripts).strip()
    if not transcript:
        raise RuntimeError("Transcription completed but returned empty text.")
    return transcript


def split_sentences(text: str) -> list[str]:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return []
    parts = re.split(r"(?<=[.!?。！？])\s+", cleaned)
    if len(parts) == 1:
        parts = re.split(r"(?<=[。！？])", cleaned)
    return [part.strip() for part in parts if part.strip()]


def summarize_transcript(transcript: str) -> tuple[str, list[str], list[str]]:
    sentences = split_sentences(transcript)
    if not sentences:
        return "", [], []

    summary_sentences = sentences[: min(5, len(sentences))]
    summary = " ".join(summary_sentences)

    key_points: list[str] = []
    stride = max(1, len(sentences) // 6)
    for index in range(0, len(sentences), stride):
        point = sentences[index]
        if point not in key_points:
            key_points.append(point)
        if len(key_points) >= 6:
            break

    action_markers = (
        "todo",
        "action",
        "next step",
        "follow up",
        "需要",
        "行动",
        "下一步",
        "跟进",
        "必须",
        "应该",
    )
    action_items = [
        sentence for sentence in sentences if any(marker in sentence.lower() for marker in action_markers)
    ][:8]

    return summary, key_points, action_items


def render_markdown(result: ProcessingResult) -> str:
    action_items = result.action_items or ["No explicit action items detected."]
    key_points = result.key_points or ["No key points generated."]
    lines = [
        f"# {result.title}",
        "",
        "## Metadata",
        "",
        f"- Source: {result.source}",
        f"- Model: {result.model}",
        f"- Language: {result.language or 'auto'}",
        f"- Created: {result.created_at}",
        "",
        "## Summary",
        "",
        result.summary or "Summary generation was skipped.",
        "",
        "## Key Points",
        "",
    ]
    lines.extend(f"- {point}" for point in key_points)
    lines.extend(
        [
            "",
            "## Action Items",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in action_items)
    lines.extend(
        [
            "",
            "## Transcript",
            "",
            result.transcript,
            "",
        ]
    )
    return "\n".join(lines)


def write_outputs(result: ProcessingResult, output_dir: Path, output_format: str) -> ProcessingResult:
    slug = safe_slug(result.title)
    markdown_path: Path | None = None
    json_path: Path | None = None

    if output_format in {"markdown", "both"}:
        markdown_path = output_dir / f"{slug}.md"
        result.artifacts.markdown = str(markdown_path)

    if output_format in {"json", "both"}:
        json_path = output_dir / f"{slug}.json"
        result.artifacts.json = str(json_path)

    if markdown_path:
        markdown_path.write_text(render_markdown(result), encoding="utf-8")

    if json_path:
        json_path.write_text(
            json.dumps(asdict(result), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return result


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    title = source_title(args.source, args.title)
    att_context_size = parse_att_context_size(args.att_context_size)

    with tempfile.TemporaryDirectory(prefix="video-report-") as temp_name:
        temp_dir = Path(temp_name)
        transcript: str | None = None
        transcript_model = args.model

        if is_url(args.source) and args.transcript_source in {"auto", "subtitles"}:
            transcript = fetch_url_subtitles(
                args.source,
                temp_dir,
                language=args.language,
                subtitle_languages=args.subtitle_languages,
                block_seconds=args.chunk_seconds,
            )
            if transcript:
                transcript_model = "yt-dlp subtitles"
            elif args.transcript_source == "subtitles":
                raise RuntimeError("No usable subtitles/transcript found for this URL.")

        if transcript is None and is_url(args.source):
            media_path = download_audio(args.source, temp_dir)
        elif transcript is None:
            media_path = Path(args.source).expanduser().resolve()
            if not media_path.exists():
                raise SystemExit(f"Local source does not exist: {media_path}")

        normalized_audio: Path | None = None
        if transcript is None:
            normalized_audio = normalize_audio(media_path, temp_dir)
            transcript = transcribe_audio(
                normalized_audio,
                model_name=args.model,
                language=args.language,
                att_context_size=att_context_size,
                chunk_seconds=args.chunk_seconds,
            )

        if args.skip_summary:
            summary, key_points, action_items = None, [], []
        else:
            summary, key_points, action_items = summarize_transcript(transcript)

        kept_audio: str | None = None
        if args.keep_workdir and normalized_audio is not None:
            kept_audio_path = output_dir / f"{safe_slug(title)}.normalized.wav"
            shutil.copy2(normalized_audio, kept_audio_path)
            kept_audio = str(kept_audio_path)

        result = ProcessingResult(
            source=args.source,
            title=title,
            model=transcript_model,
            language=args.language,
            created_at=datetime.now(timezone.utc).isoformat(),
            transcript=transcript,
            summary=summary,
            key_points=key_points,
            action_items=action_items,
            artifacts=ArtifactPaths(
                output_dir=str(output_dir),
                markdown=None,
                json=None,
                audio=kept_audio,
            ),
        )
        result = write_outputs(result, output_dir, args.format)

    print(json.dumps(asdict(result.artifacts), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
