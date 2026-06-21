---
name: video-report-nemotron
description: "Use when the user gives a YouTube, Bilibili, or other video URL or local media file and wants a transcript, summary, chapters, action items, visual evidence, or Markdown/HTML/PDF report. Prefers existing subtitles, then falls back to Nemotron ASR."
platforms: [macos, linux, windows]
---

# Video Report Nemotron

## When To Use

Use this skill when the user asks to analyze, summarize, transcribe, or report on a video/audio URL or local media file. For URLs, prefer existing subtitles/transcripts when available; fall back to ASR only when subtitles are unavailable or the user explicitly requests ASR. ASR must use a Nemotron backend.

If the user asks for a report, comprehensive report, deep report, visual report, Markdown/PDF report, or a result meant for end users, do not stop after `video_report.py`. The Markdown from `video_report.py` is an intermediate transcript artifact. The final user-facing artifact must be produced by `video_compose_final_report.py`, with Markdown, HTML, and PDF when requested or when the user asks for a full report.

When invoking ASR from Hermes, use `--asr-backend auto` unless the user explicitly asks for a Nemotron backend. Do not add non-Nemotron transcription fallbacks. The current local backend is `mlx-nemotron`: Apple Silicon plus installed `mlx_audio` uses `mlx-community/nemotron-3.5-asr-streaming-0.6b-8bit`. If Hermes launches the script with a Python that lacks `mlx_audio`, the script tries nearby project virtualenvs such as `../.venv-nemotron/bin/python` before failing clearly.

For visual reports and full user-facing reports, do not trigger screenshots from raw keywords. First review each timestamped transcript block and write a visual manifest that says whether that block needs original video evidence. Capture frames only for reviewed blocks, run OCR first, and use multimodal image analysis only for frames where OCR is weak or insufficient.

Hard invariant for URL videos: if the user asks for a report rather than a transcript-only summary, obtain a stable local video source for frame capture whenever video formats are available. Audio-only downloads are sufficient for ASR, but they are not sufficient for figures, slide/table extraction, OCR, or visual fact-checking. `video_capture_frames.py` now downloads a local video copy by default for URL sources, captures frames from it, records the path in `frame_capture_source.local_video_path`, and deletes the downloaded video by default after frame capture. Use `--keep-video` only when the user explicitly asks to retain the local video file. Only skip video download/capture when the user explicitly requests a text-only report or when video formats are genuinely unavailable; in that case, state that the report has no visual evidence.

Hard invariant for rendering: never hand-roll Markdown-to-HTML/PDF with ad hoc paragraph splitting. Use `video_compose_final_report.py` for composed reports, `video_build_visual_report.py` for visual audit reports, or `video_render_markdown.py` for existing Markdown. These renderers preserve tables, `**bold**`, links, lists, code blocks, and images. If a PDF fallback is needed, render Markdown to HTML first with `video_render_markdown.py`, then let its Playwright/`uvx --from playwright` fallback make the PDF.

Output-root invariant on this machine: keep generated reports, downloaded media, extracted audio, normalized ASR audio, frames, OCR, and diagnostics under the mounted drive by default: `/Volumes/SN770Coder/Data/video_reports/<video-id-or-slug>/`. `video_report.py` defaults to `VIDEO_REPORT_OUTPUT_ROOT/<source-slug>` when `VIDEO_REPORT_OUTPUT_ROOT` is set, otherwise `/Volumes/SN770Coder/Data/video_reports/<source-slug>` when that drive is mounted. Do not write new report artifacts under `~/video_reports` unless the user explicitly asks.

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
python3 SKILL_DIR/scripts/video_report.py "https://www.bilibili.com/video/BV..." --language zh-CN
python3 SKILL_DIR/scripts/video_report.py ./meeting.mp4 --title "Team meeting" --output-dir ./reports
python3 SKILL_DIR/scripts/video_report.py ./audio.wav --skip-summary --format json
```

For normal Hermes runs, keep ASR backend selection automatic:

```bash
python3 SKILL_DIR/scripts/video_report.py "https://www.youtube.com/watch?v=VIDEO_ID" \
  --transcript-source auto \
  --asr-backend auto \
  --language zh-CN \
  --output-dir /Volumes/SN770Coder/Data/video_reports/VIDEO_ID
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
#    For URL sources this downloads a stable local video copy, captures frames,
#    then deletes the downloaded video unless --keep-video is passed.
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

# Render an existing fact-check Markdown or manually written addendum without losing tables/bold.
python3 SKILL_DIR/scripts/video_render_markdown.py visual/factcheck.md \
  --html visual/factcheck.html \
  --pdf visual/factcheck.pdf
```

Read `visual_manifest.json` before capture when accuracy matters. It is valid to edit `needs_video`, `priority`, `visual_questions`, and `sampling.times` manually or with an LLM. The invariant is: visual capture follows reviewed block annotations, not direct keyword matching.

Read `references/report-quality-pitfalls.md` when producing user-facing reports. It captures hard lessons about visual evidence, temporary video cleanup, Markdown/PDF legibility, and finance/economics fact-checking.

Read `visual_manifest.json` before capture when accuracy matters. It is valid to edit `needs_video`, `priority`, `visual_questions`, and `sampling.times` manually or with an LLM. The invariant is: visual capture follows reviewed block annotations, not direct keyword matching.

Read `references/report-quality-pitfalls.md` when producing user-facing reports. It captures hard lessons about visual evidence, temporary video cleanup, Markdown/PDF legibility, and finance/economics fact-checking.

The final user-facing output should come from `video_compose_final_report.py`: it writes a rich explanatory report from the full transcript and inserts selected images where they support the narrative. Use `video_build_visual_report.py --style evidence` only when the user explicitly wants the full OCR/vision audit trail.

## Workflow

1. Run the helper script on the URL or local path.
2. If the source is a URL and subtitles are available, the script uses `yt-dlp` subtitles as the transcript.
3. If subtitles are unavailable, the script downloads/extracts audio, normalizes it to 16 kHz mono WAV, and transcribes with the configured `--asr-backend`.
4. The script writes intermediate Markdown/JSON transcript artifacts, then prints paths to stdout.
5. For any user-facing report request, ensure a video source is available for visual evidence:
   - If `video_report.py` only downloaded audio, run `video_capture_frames.py` normally; for URL sources it will download a temporary local video copy, capture frames, and delete the video by default.
   - If the user wants to keep the downloaded video, pass `--keep-video`; otherwise do not leave a local video file behind.
   - If URL video formats are blocked but audio/transcript exists, say explicitly that visual evidence is unavailable and the report is text-only.
   - Do not claim image/OCR findings unless `video_capture_frames.py` and `video_ocr_frames.py` actually ran on captured frames.
6. For any user-facing report request, run the visual report pipeline and final composer:
   - `video_visual_manifest.py`
   - `video_capture_frames.py`
   - `video_ocr_frames.py`
   - `video_multimodal_frames.py`
   - `video_compose_final_report.py`
   - If you are producing a text-only/non-visual report, still create a minimal visual manifest with `video_visual_manifest.py` and pass it as the required second positional argument to `video_compose_final_report.py`; do not invoke the composer with only `report.json`.
7. Treat only the `video_compose_final_report.py` Markdown/HTML/PDF as the final report, unless you are writing a special fact-check addendum; render such addenda with `video_render_markdown.py`.
8. Inspect the final Markdown and rendered HTML/PDF before replying. Tables must render as tables, `**bold**` must render as bold, images must display, and the report must be free of internal pipeline wording.

## Verification and Fact-Checking Add-on

When the user asks to verify truthfulness, authenticity, or factual accuracy (e.g. “验证真实性”, “fact check”, “核验”), treat the transcript/report as source material, not as verified output:

1. Generate the transcript artifact first (`video_report.py` or the full visual pipeline when needed).
2. Inspect the transcript directly rather than trusting the script’s auto-generated `Summary`, `Key Points`, or `Action Items`; ASR and summarization can garble names, tickers, percentages, and action-item extraction.
3. If the URL page can be extracted by Firecrawl/web extraction but no subtitles, transcript, audio, or playable media are available, treat that as **metadata-only access**. Do not infer the video's arguments from title, description, recommendations, ads, or page JSON. Ask for or obtain one of: transcript text, local audio/video, usable browser cookies, or a Firecrawl result that explicitly contains the transcript/口播正文.
4. When only metadata is available, deliver at most a limited report that clearly labels the coverage as `页面元数据/标题主题核验`, and fact-check only externally checkable claims that appear in the title/description. State that the substantive video content remains unverified.
5. Extract the video’s checkable claims into two buckets:
   - hard factual claims: dates, numbers, named institutions, court rulings, IPO prices, survey values, cited reports;
   - commentary/interpretation: causal claims, trend judgments, political/economic conclusions.
6. Verify hard claims with live search/extraction from multiple sources when practical, preserving source names/URLs in the final answer.
7. Mark each claim as `属实/基本属实/部分可验证/无法确认/观点判断`, and explicitly separate “事实核验” from “作者观点”.
8. For long videos, keep the final user-facing result compact: a short thesis, a verification table, and a concise clause/bullet list unless the user asks for a full report.

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
- Include visual claims only when backed by captured frames plus OCR/vision output; otherwise label the report as text-only.
- Avoid internal terms such as manifest, OCR, ASR, transcript block, evidence directory, visual questions, needs image, or screenshot rationale.
- Include Markdown and PDF paths when the user asks for a full report.

When transcript text is long, let the final composer chunk it. Preserve exact file paths from the final composer output so the user can inspect the artifacts.

## Error Handling

- Missing `yt-dlp`: install with `pip install yt-dlp`, or create/use a shell wrapper that runs `uvx --from yt-dlp yt-dlp` when the script needs a `yt-dlp` executable on `PATH`.
- YouTube `Sign in to confirm you’re not a bot`: before asking for a local file, try browser login cookies in this order when available: `yt-dlp --cookies-from-browser safari`, then `chrome`, then other installed Chromium-family browsers. On macOS, Safari cookies may fail with `Operation not permitted ... Cookies.binarycookies`; the fix is granting Full Disk Access to the running terminal/Hermes app or having the user export a Netscape-format `cookies.txt`, then rerun with `yt-dlp --cookies /path/to/cookies.txt`. Never ask for or type Google passwords/2FA; open the page for the user to login manually if UI access works.
- If Python starts with a mismatched `PYTHONHOME`/`PYTHONPATH` and fails before imports with `ModuleNotFoundError: No module named 'encodings'`, rerun the video-report commands with those variables cleared, e.g. `env -u PYTHONHOME -u PYTHONPATH ...`. If subprocesses such as `yt-dlp` are launched from inside the scripts, ensure any wrapper also clears these variables before invoking Python-based CLIs.
- For local audio transcription on macOS, when the launch Python lacks `mlx_audio` but a nearby Nemotron venv exists, run with both variables cleared and explicitly pin the venv: `env -u PYTHONHOME -u PYTHONPATH VIDEO_REPORT_PYTHON=/path/to/.venv-nemotron/bin/python /path/to/.venv-nemotron/bin/python SKILL_DIR/scripts/video_report.py input.m4a --transcript-source asr --asr-backend auto --language zh-CN --format both --skip-summary --keep-workdir --chunk-seconds 60`. This avoids Hermes/PYTHONPATH pollution and forces the working MLX runtime.
- If MLX/Nemotron ASR prints `503 Service Unavailable` while fetching model files, treat it as a transient Hugging Face/model-cache fetch failure, not an audio failure. Retry after verifying the cache exists; use shorter chunks such as `--chunk-seconds 60` to reduce rerun cost and memory pressure.
- Missing `ffmpeg`: install with `brew install ffmpeg`.
- Missing `ImageMagick`: LiteParse image OCR needs `brew install imagemagick`.
- Missing `liteparse`: install with `npm install -g @run-llama/liteparse`.
- Missing PDF support: install Playwright browsers with `playwright install chromium`. If the composer reports `playwright command not found` but `uv` is available, generate the PDF directly with `uvx --from playwright playwright pdf file:///absolute/report.html /absolute/report.pdf`.
- Frame capture from a YouTube direct `googlevideo.com` URL may fail in `ffmpeg` with TLS EOF or expired signed URL errors. Robust workaround: download a local MP4 first with `yt-dlp -f 'best[height<=720][ext=mp4]/18/best' -o 'source.%(ext)s' URL`, then rerun `video_capture_frames.py ... --video-source /absolute/source.mp4 --overwrite`.
- YouTube videos may expose metadata/live chat but no audio/video formats when PO Token / Proof-of-Origin and `n challenge` enforcement is active. Distinguish the cases:
  - `Extracted N cookies from safari` followed by `Only images are available` means cookies are readable but media formats are blocked, not that Safari login failed.
  - `Detected experiment to bind GVS PO Token` plus `Retrieved a gvs PO Token` means the PO Token provider is working; if formats are still only `sb*` storyboard, the remaining blocker is usually `n challenge` player JS download/solver failure or network/proxy truncation.
  - Official yt-dlp fix path: install a PO Token provider plugin such as `bgutil-ytdlp-pot-provider`, start its local HTTP server on `127.0.0.1:4416`, then run `yt-dlp` with the plugin, fresh browser cookies, and an appropriate YouTube client such as `mweb`.
  - Minimal local setup: `git clone --single-branch --branch 1.3.1 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git ~/tools/bgutil-ytdlp-pot-provider && cd ~/tools/bgutil-ytdlp-pot-provider/server && npm ci && npx tsc && node build/main.js --port 4416`; run yt-dlp as `uvx --from yt-dlp --with bgutil-ytdlp-pot-provider yt-dlp --cookies-from-browser safari --extractor-args 'youtube:player_client=mweb' URL`.
  - If the provider logs show `Retrieved a gvs PO Token` but `Failed to load player for JS challenge: Download of ... base.js failed` remains, do not try system-audio recording as the default workaround. Treat it as network/proxy/player-JS truncation and either fix proxy access to `youtube.com/s/player/.../base.js`, provide a local audio/video file, or manually export/copy the YouTube transcript if available.
  - On macOS with Shadowrocket/HTTP proxy, Safari and `curl` may download YouTube normally while Python/urllib inside `yt-dlp` repeatedly truncates `base.js`. Use browser impersonation with curl-cffi: `uvx --from yt-dlp --with bgutil-ytdlp-pot-provider --with curl-cffi yt-dlp --impersonate safari --remote-components ejs:github --cookies-from-browser safari --proxy http://127.0.0.1:1082 --extractor-args 'youtube:player_client=mweb' -f 140 -o 'source.%(ext)s' URL`. This can unlock formats after PO Token provider is running.
- Bilibili URLs can fail before metadata extraction with `HTTP Error 412: Precondition Failed` / `错误号: 412`. Treat this as an access-context/risk-control block, not an ASR or subtitle failure. First try a logged-in cookie source (`yt-dlp --cookies-from-browser chrome` or `--cookies cookies.txt`); if unavailable, ask for a local MP4/audio file and run the pipeline on that local file. See `references/bilibili-412.md` for details.
- `references/youtube-cookies-po-token.md` — YouTube can allow metadata and browser cookies but still provide no speech transcript and no audio/video formats because of captions absence, player JS challenge, or PO Token playback-integrity requirements. Use the diagnostic ladder in `references/youtube-cookies-po-token.md`; if only storyboard/live_chat is available, switch to a user-provided transcript/local media file or an explicitly consented browser-playback recording fallback. Do not generate spoken-content reports from title/metadata alone.
- `references/youtube-video-report-hardening.md` — hardened report workflow learned from user correction: mounted-drive artifact root, video-backed visual evidence, no retained local video unless requested, correct Markdown/PDF rendering, and a working YouTube download validation pattern.
- For economics, investing, market-structure, gambling/probability, or finance-adjacent videos that require truth verification, use `references/finance-video-factcheck-workflow.md`: extract spoken content first, inspect the transcript directly, separate hard claims from commentary, recompute formulas/percentages, cross-check with official/academic/primary sources, and write a compact verification table plus beginner-learning section.
- Missing ASR backend: install a supported Nemotron backend. For the current Apple Silicon path, install `mlx-audio`.
- Missing Nemotron support: install `mlx-audio` from GitHub, because Nemotron support may not be in the latest PyPI release.
- Unsupported/private URL: ask the user for an accessible URL or a local media file.
- When not using Apple Silicon/MLX: do not substitute Whisper-family ASR. Add or install an explicit Nemotron backend for that platform first.
