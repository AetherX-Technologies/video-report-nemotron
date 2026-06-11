---
name: video-report-nemotron
description: "Use when the user gives a YouTube, Bilibili, or other video URL or local media file and wants an audio transcript, summary, chapters, action items, or a Markdown/JSON report. Downloads or extracts audio with yt-dlp/ffmpeg, transcribes locally on Apple Silicon with mlx-community/nemotron-3.5-asr-streaming-0.6b-8bit, then formats the result into reusable report files."
platforms: [macos]
---

# Video Report Nemotron

## When To Use

Use this skill when the user asks to analyze, summarize, transcribe, or report on a video/audio URL or local media file. For URLs, prefer existing subtitles/transcripts when available; fall back to Apple Silicon local transcription with Nemotron 3.5 ASR through `mlx-audio` only when subtitles are unavailable or the user explicitly requests ASR.

For visual reports, do not trigger screenshots from raw keywords. First review each timestamped transcript block and write a visual manifest that says whether that block needs original video evidence. Capture frames only for reviewed blocks, run OCR first, and use multimodal image analysis only for frames where OCR is weak or insufficient.

## Setup

Install runtime dependencies:

```bash
brew install ffmpeg
pip install yt-dlp "git+https://github.com/Blaizzy/mlx-audio.git"
```

For visual OCR on extracted video frames, install LiteParse and ImageMagick:

```bash
npm install -g @run-llama/liteparse
brew install imagemagick
```

For PDF output, install Playwright browsers if needed:

```bash
playwright install chromium
```

For multimodal fallback configuration, use `.env.example` as the machine-readable template. Runtime config is loaded from `OPENAI_*` environment variables, an explicit `--env-file`, `./.env`, the skill root `.env`, or `~/.config/video-report-nemotron/.env`. Store real API keys in local `.env` files or the shell environment, not in tracked skill files.

For a clean local install, prefer an isolated Python 3.12 environment:

```bash
uv venv .venv-nemotron --python 3.12
uv pip install --python .venv-nemotron/bin/python yt-dlp "git+https://github.com/Blaizzy/mlx-audio.git"
```

The ASR model is `mlx-community/nemotron-3.5-asr-streaming-0.6b-8bit`. The first run downloads model weights from Hugging Face.

## Helper Script

`SKILL_DIR` is the directory containing this `SKILL.md`.

```bash
python3 SKILL_DIR/scripts/video_report.py "https://www.youtube.com/watch?v=VIDEO_ID"
python3 SKILL_DIR/scripts/video_report.py "https://www.bilibili.com/video/BV..." --language zh-CN
python3 SKILL_DIR/scripts/video_report.py ./meeting.mp4 --title "Team meeting" --output-dir ./reports
python3 SKILL_DIR/scripts/video_report.py ./audio.wav --skip-summary --format json
```

Useful options:

- `--transcript-source`: `auto` prefers URL subtitles and falls back to ASR; `subtitles` requires subtitles; `asr` always runs local ASR.
- `--subtitle-languages`: comma-separated yt-dlp subtitle language selector.
- `--language`: force a Nemotron language prompt such as `en-US`, `zh-CN`, `ja-JP`, `fr-FR`; omit for auto detection.
- `--model`: override the MLX model repository.
- `--output-dir`: choose where generated files are written.
- `--keep-workdir`: keep downloaded and normalized audio for debugging.
- `--skip-summary`: produce transcript-only Markdown/JSON.
- `--format`: `markdown`, `json`, or `both`.
- `--chunk-seconds`: split long audio before ASR to avoid MLX/Metal memory spikes.

## Visual Report Pipeline

Use this after `video_report.py` has produced JSON with timestamped transcript blocks.

```bash
# 1. Create a block-level visual manifest. This is a review artifact, not a keyword trigger.
python3 SKILL_DIR/scripts/video_visual_manifest.py report.json \
  -o visual/visual_manifest.json

# 2. Capture frames only for manifest blocks marked needs_video=true.
python3 SKILL_DIR/scripts/video_capture_frames.py visual/visual_manifest.json \
  -o visual/visual_manifest.frames.json \
  --frames-dir visual/frames

# 3. OCR frames with LiteParse. The process exits after each run; do not keep OCR resident.
python3 SKILL_DIR/scripts/video_ocr_frames.py visual/visual_manifest.frames.json \
  -o visual/visual_manifest.ocr.json \
  --ocr-dir visual/ocr

# 4. Run multimodal fallback only for OCR-weak frames.
python3 SKILL_DIR/scripts/video_multimodal_frames.py visual/visual_manifest.ocr.json \
  -o visual/visual_manifest.vision.json \
  --env-file SKILL_DIR/.env

# 5. Compose the final user-facing report. This is the report to show users.
python3 SKILL_DIR/scripts/video_compose_final_report.py report.json visual/visual_manifest.vision.json \
  --markdown visual/report.md \
  --html visual/report.html \
  --pdf visual/report.pdf \
  --env-file SKILL_DIR/.env
```

Review `visual_manifest.json` before capture when accuracy matters. It is valid to edit `needs_video`, `priority`, `visual_questions`, and `sampling.times` manually or with an LLM. The invariant is: visual capture follows reviewed block annotations, not direct keyword matching.

The final user-facing output should come from `video_compose_final_report.py`: it writes a rich explanatory report from the full transcript and inserts selected images where they support the narrative. Use `video_build_visual_report.py --style evidence` only when the user explicitly wants the full OCR/vision audit trail.

## Workflow

1. Run the helper script on the URL or local path.
2. If the source is a URL and subtitles are available, the script uses `yt-dlp` subtitles as the transcript.
3. If subtitles are unavailable, the script downloads/extracts audio, normalizes it to 16 kHz mono WAV, and transcribes with `mlx-community/nemotron-3.5-asr-streaming-0.6b-8bit`.
5. The script writes Markdown and/or JSON artifacts, then prints paths to stdout.
6. Read the Markdown report. If the user asked for a specific format, transform the transcript into that format.
7. If the user wants a visual report, generate and review the visual manifest before extracting frames.

## Output Guidance

Default report sections:

- Source and processing metadata
- Transcript
- Short summary
- Key points
- Action items

When transcript text is long, chunk it before producing a final response. Preserve exact file paths from the script output so the user can inspect the artifacts.

## Error Handling

- Missing `yt-dlp`: install with `pip install yt-dlp`.
- Missing `ffmpeg`: install with `brew install ffmpeg`.
- Missing `ImageMagick`: LiteParse image OCR needs `brew install imagemagick`.
- Missing `liteparse`: install with `npm install -g @run-llama/liteparse`.
- Missing PDF support: install Playwright browsers with `playwright install chromium`.
- Missing Nemotron support: install `mlx-audio` from GitHub, because Nemotron support may not be in the latest PyPI release.
- Unsupported/private URL: ask the user for an accessible URL or a local media file.
- Non-Apple Silicon machine: explain that this skill is designed for MLX on Apple Silicon and suggest a Whisper/faster-whisper fallback skill.
