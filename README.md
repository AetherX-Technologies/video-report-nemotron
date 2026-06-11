# Video Report Nemotron

Video Report Nemotron is a Hermes skill for turning video links or local media files into structured reports. It prefers existing platform subtitles when they are available, falls back to local Apple Silicon ASR with `mlx-community/nemotron-3.5-asr-streaming-0.6b-8bit`, and can build Markdown, HTML, and PDF reports with selected video frames.

[中文文档](docs/README.zh-CN.md)

![Rendered report preview](docs/assets/report-html-preview.png)

## What It Does

- Accepts YouTube, Bilibili, other `yt-dlp` supported URLs, or local media files.
- Uses platform subtitles first, so local ASR is only invoked when subtitles are unavailable or explicitly requested.
- Runs local Apple Silicon transcription through MLX/Nemotron when ASR is needed.
- Creates transcript-first Markdown/JSON artifacts for reuse.
- Builds a visual report pipeline: transcript block review, frame capture, OCR, optional multimodal fallback, then a final user-facing report.
- Exports Markdown, HTML, and PDF with proper tables, lists, blockquotes, inline formatting, images, and captions.

## Example Output

This repository includes a generated example from a public YouTube video:

- [Markdown report](examples/QggkUtXNkPo/report.md)
- [HTML report](examples/QggkUtXNkPo/report.html)
- [PDF report](examples/QggkUtXNkPo/report.pdf)

The final report embeds selected frames only where the visual context helps explain the content.

![Visual evidence frame](docs/assets/visual-evidence-frame.png)

## Repository Layout

```text
.
├── skill/                     # Hermes skill package
│   ├── SKILL.md               # Skill instructions
│   ├── .env.example           # Runtime config template, no real keys
│   ├── scripts/               # Download, ASR, visual, OCR, report scripts
│   └── tests/                 # Pytest coverage for the pipeline
├── docs/
│   ├── README.zh-CN.md        # Chinese documentation
│   └── assets/                # README screenshots
└── examples/
    └── QggkUtXNkPo/           # Example final report artifacts
```

## Requirements

The skill is designed for macOS on Apple Silicon.

Install system tools:

```bash
brew install ffmpeg imagemagick
npm install -g @run-llama/liteparse
playwright install chromium
```

Create a Python environment:

```bash
uv venv .venv-nemotron --python 3.12
uv pip install --python .venv-nemotron/bin/python \
  yt-dlp pytest "git+https://github.com/Blaizzy/mlx-audio.git"
```

The default ASR model is:

```text
mlx-community/nemotron-3.5-asr-streaming-0.6b-8bit
```

The model is downloaded through the Hugging Face cache on first use.

## Configuration

Copy the template and set your local values:

```bash
cp skill/.env.example skill/.env
```

`skill/.env.example` contains OpenAI-compatible settings for text composition and multimodal visual fallback:

```dotenv
OPENAI_BASE_URL=https://sub2api.gptclubapi.xyz/v1
OPENAI_VISION_MODEL=gpt-5.5
OPENAI_TEXT_MODEL=gpt-5.5
OPENAI_API_KEY=
```

Never commit a real API key. The tracked file is only a template.

## Quick Start

Generate a transcript-first report:

```bash
.venv-nemotron/bin/python skill/scripts/video_report.py \
  "https://www.youtube.com/watch?v=QggkUtXNkPo" \
  --language zh-CN \
  --output-dir reports/QggkUtXNkPo \
  --chunk-seconds 90
```

By default, `--transcript-source auto` tries subtitles first and uses local ASR only when subtitles are unavailable. To force local ASR:

```bash
.venv-nemotron/bin/python skill/scripts/video_report.py ./video.mp4 \
  --transcript-source asr \
  --language zh-CN \
  --output-dir reports/local-video
```

## Visual Report Pipeline

After `video_report.py` produces a JSON artifact, run the visual pipeline:

```bash
# 1. Review transcript blocks and decide which ones need video evidence.
.venv-nemotron/bin/python skill/scripts/video_visual_manifest.py \
  reports/QggkUtXNkPo/QggkUtXNkPo.json \
  -o reports/QggkUtXNkPo/visual/visual_manifest.json

# 2. Capture frames only for reviewed blocks.
.venv-nemotron/bin/python skill/scripts/video_capture_frames.py \
  reports/QggkUtXNkPo/visual/visual_manifest.json \
  -o reports/QggkUtXNkPo/visual/visual_manifest.frames.json \
  --frames-dir reports/QggkUtXNkPo/visual/frames

# 3. Run OCR on frames.
.venv-nemotron/bin/python skill/scripts/video_ocr_frames.py \
  reports/QggkUtXNkPo/visual/visual_manifest.frames.json \
  -o reports/QggkUtXNkPo/visual/visual_manifest.ocr.json \
  --ocr-dir reports/QggkUtXNkPo/visual/ocr

# 4. Use multimodal fallback only when OCR is weak.
.venv-nemotron/bin/python skill/scripts/video_multimodal_frames.py \
  reports/QggkUtXNkPo/visual/visual_manifest.ocr.json \
  -o reports/QggkUtXNkPo/visual/visual_manifest.vision.json \
  --env-file skill/.env

# 5. Compose the final user-facing report.
.venv-nemotron/bin/python skill/scripts/video_compose_final_report.py \
  reports/QggkUtXNkPo/QggkUtXNkPo.json \
  reports/QggkUtXNkPo/visual/visual_manifest.vision.json \
  --markdown reports/QggkUtXNkPo/visual/report.md \
  --html reports/QggkUtXNkPo/visual/report.html \
  --pdf reports/QggkUtXNkPo/visual/report.pdf \
  --env-file skill/.env
```

## Installing as a Hermes Skill

Copy the skill folder into your Hermes skills directory:

```bash
mkdir -p ~/.hermes/skills/media
rsync -a skill/ ~/.hermes/skills/media/video-report-nemotron/
```

Then ask Hermes to analyze, transcribe, summarize, or generate a report from a video URL or local media file.

## Testing

```bash
.venv-nemotron/bin/python -m pytest skill/tests
```

The tests cover transcript source behavior, visual manifest selection, environment loading, report composition, and Markdown-to-HTML rendering for PDF output.

## Security Notes

- Real `.env` files are ignored.
- Media downloads and normalized audio/video files are ignored.
- The example report is included as a small reproducible artifact; raw downloaded media and model weights are not committed.
- Public video content may still be subject to the source platform's terms and copyright. Use the tool on content you are allowed to process.

