---
name: video-report-nemotron
description: "Use when the user gives a YouTube or other supported video URL or local media file and wants a transcript, summary, chapters, action items, visual evidence, or Markdown/HTML/PDF report. Prefers existing subtitles, then falls back to Nemotron ASR."
platforms: [macos, linux, windows]
---

# Video Report Nemotron

## When To Use

Use this skill when the user asks to analyze, summarize, transcribe, or report on a video/audio URL or local media file. For URLs, prefer existing subtitles/transcripts when available; fall back to ASR only when subtitles are unavailable or the user explicitly requests ASR. ASR must use a Nemotron backend.

If the user asks for a report, comprehensive report, deep report, visual report, Markdown/PDF report, or a result meant for end users, do not stop after `video_report.py`. The Markdown from `video_report.py` is an intermediate transcript artifact. The final user-facing artifact must be produced by `video_compose_final_report.py`, with Markdown, HTML, and PDF when requested or when the user asks for a full report.

When invoking ASR from Hermes, use `--asr-backend auto` unless the user explicitly asks for a Nemotron backend. Do not add non-Nemotron transcription fallbacks. The current local backend is `mlx-nemotron`: Apple Silicon plus installed `mlx_audio` uses `mlx-community/nemotron-3.5-asr-streaming-0.6b-8bit`. If Hermes launches the script with a Python that lacks `mlx_audio`, the script tries nearby project virtualenvs such as `../.venv-nemotron/bin/python` before failing clearly.

For visual reports, do not trigger screenshots from raw keywords. First review each timestamped transcript block and write a visual manifest that says whether that block needs original video evidence. Capture frames only for reviewed blocks, run OCR first, and use multimodal image analysis only for frames where OCR is weak or insufficient.

## Setup

Install runtime dependencies:

```bash
brew install ffmpeg
pip install yt-dlp httpx
```

Install Nemotron ASR if you need subtitle fallback:

```bash
# Apple Silicon optimized ASR
pip install "git+https://github.com/Blaizzy/mlx-audio.git"
```

Other Nemotron runtimes, such as ONNX INT4 or NeMo, must be added as explicit Nemotron backends before use. Do not substitute Whisper-family models.

For visual OCR on extracted video frames, install LiteParse and ImageMagick:

```bash
npm install -g @run-llama/liteparse
brew install imagemagick
```

For PDF output, install Playwright browsers if needed:

```bash
playwright install chromium
```

For text composition, visual fallback, ASR backend selection, and report language, use `.env.example` as the machine-readable template. Runtime config is loaded from `OPENAI_*`, `ASR_BACKEND`, `MLX_ASR_MODEL`, and `REPORT_LANGUAGE` environment variables, an explicit `--env-file`, `./.env`, the skill root `.env`, or `~/.config/video-report-nemotron/.env`. Store real API keys in local `.env` files or the shell environment, not in tracked skill files.

For a clean local install, prefer an isolated Python 3.12 environment:

```bash
uv venv .venv-nemotron --python 3.12
uv pip install --python .venv-nemotron/bin/python yt-dlp httpx pytest
uv pip install --python .venv-nemotron/bin/python "git+https://github.com/Blaizzy/mlx-audio.git"
```

The Apple Silicon ASR profile uses `mlx-community/nemotron-3.5-asr-streaming-0.6b-8bit`. The first run downloads model weights from Hugging Face.

## Helper Script

`SKILL_DIR` is the directory containing this `SKILL.md`.

```bash
python3 SKILL_DIR/scripts/video_report.py "https://www.youtube.com/watch?v=VIDEO_ID"
python3 SKILL_DIR/scripts/video_report.py "https://example.com/video" --language zh-CN
python3 SKILL_DIR/scripts/video_report.py ./meeting.mp4 --title "Team meeting" --output-dir ./reports
python3 SKILL_DIR/scripts/video_report.py ./audio.wav --skip-summary --format json
```

For normal Hermes runs, keep ASR backend selection automatic:

```bash
python3 SKILL_DIR/scripts/video_report.py "https://www.youtube.com/watch?v=VIDEO_ID" \
  --transcript-source auto \
  --asr-backend auto \
  --language zh-CN \
  --output-dir ./reports/VIDEO_ID
```

Do not pass `--model small` or force Whisper-family models.

Useful options:

- `--transcript-source`: `auto` prefers URL subtitles and falls back to ASR; `subtitles` requires subtitles; `asr` always runs local ASR.
- `--asr-backend`: `auto` or `mlx-nemotron`. Prefer `auto` in normal Hermes use so the script uses the current machine's installed Nemotron backend.
- `--subtitle-languages`: comma-separated yt-dlp subtitle language selector.
- `--language`: force a language hint such as `en-US`, `zh-CN`, `ja-JP`, `fr-FR`; omit for auto detection.
- `--model`: override the selected ASR backend model.
- `--env-file`: load local provider/backend config.
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
  --env-file SKILL_DIR/.env \
  --analysis-language auto

# 5. Compose the final user-facing report. This is the report to show users.
python3 SKILL_DIR/scripts/video_compose_final_report.py report.json visual/visual_manifest.vision.json \
  --markdown visual/report.md \
  --html visual/report.html \
  --pdf visual/report.pdf \
  --env-file SKILL_DIR/.env \
  --report-language auto
```

Review `visual_manifest.json` before capture when accuracy matters. It is valid to edit `needs_video`, `priority`, `visual_questions`, and `sampling.times` manually or with an LLM. The invariant is: visual capture follows reviewed block annotations, not direct keyword matching.

The final user-facing output should come from `video_compose_final_report.py`: it writes a rich explanatory report from the full transcript and inserts selected images where they support the narrative. Use `video_build_visual_report.py --style evidence` only when the user explicitly wants the full OCR/vision audit trail.

## Workflow

1. Run the helper script on the URL or local path.
2. If the source is a URL and subtitles are available, the script uses `yt-dlp` subtitles as the transcript.
3. If subtitles are unavailable, the script downloads/extracts audio, normalizes it to 16 kHz mono WAV, and transcribes with the configured `--asr-backend`.
4. The script writes intermediate Markdown/JSON transcript artifacts, then prints paths to stdout.
5. For any user-facing report request, run the visual report pipeline and final composer:
   - `video_visual_manifest.py`
   - `video_capture_frames.py`
   - `video_ocr_frames.py`
   - `video_multimodal_frames.py`
   - `video_compose_final_report.py`
6. Treat only the `video_compose_final_report.py` Markdown/HTML/PDF as the final report.
7. Inspect the final Markdown before replying. It must be rich, structured, image-aware, and free of internal pipeline wording.

## Output Guidance

Intermediate `video_report.py` sections:

- Source and processing metadata
- Transcript
- Short summary
- Key points
- Action items

Final reports must be produced by `video_compose_final_report.py`. They must:

- Explain the video's substantive content in a polished reader-facing structure.
- Preserve concrete details, numbers, cases, thresholds, caveats, and the speaker's original argument.
- Insert key images near the relevant discussion instead of dumping screenshots at the end.
- Avoid internal terms such as manifest, OCR, ASR, transcript block, evidence directory, visual questions, needs image, or screenshot rationale.
- Include Markdown and PDF paths when the user asks for a full report.

When transcript text is long, let the final composer chunk it. Preserve exact file paths from the final composer output so the user can inspect the artifacts.

## Error Handling

- Missing `yt-dlp`: install with `pip install yt-dlp`.
- Missing `ffmpeg`: install with `brew install ffmpeg`.
- Missing `ImageMagick`: LiteParse image OCR needs `brew install imagemagick`.
- Missing `liteparse`: install with `npm install -g @run-llama/liteparse`.
- Missing PDF support: install Playwright browsers with `playwright install chromium`.
- Missing ASR backend: install a supported Nemotron backend. For the current Apple Silicon path, install `mlx-audio`.
- Missing Nemotron support: install `mlx-audio` from GitHub, because Nemotron support may not be in the latest PyPI release.
- Unsupported/private URL: ask the user for an accessible URL or a local media file.
- When not using Apple Silicon/MLX: do not substitute Whisper-family ASR. Add or install an explicit Nemotron backend for that platform first.
