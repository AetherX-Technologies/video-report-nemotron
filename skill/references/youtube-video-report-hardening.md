# YouTube Video Report Hardening

Use this reference when a YouTube report must include real video-backed visual evidence and durable artifacts.

## Required artifact root

On Glu Tri's machine, default durable outputs to the mounted drive:

```text
/Volumes/SN770Coder/Data/video_reports/<video-id-or-slug>/
```

Keep generated reports, downloaded media, extracted audio, normalized ASR audio, frames, OCR, vision manifests, and diagnostics there unless the user explicitly asks for another location. Avoid creating new report artifacts under `~/video_reports`.

## Download validation pattern

A report that claims visual evidence must have a real video file or local user-provided media. Audio-only downloads are not enough.

1. Download video+audio directly to the mounted-drive report directory.
2. Verify with `ffprobe` or equivalent metadata checks:
   - at least one video stream;
   - at least one audio stream when speech is needed;
   - expected duration;
   - non-zero size;
   - usable dimensions.
3. Extract a test frame with `ffmpeg` before running the full visual pipeline.
4. If video cannot be downloaded but transcript/audio exists, label the report as text-only and do not claim OCR/image-derived facts.

## Working YouTube pattern from this session

When normal `yt-dlp` hits YouTube bot/player restrictions but browser cookies are readable, this pattern unlocked media formats:

```bash
# Start PO Token provider separately as a managed background process.
node build/main.js --port 4416

# Download MP4 to mounted drive with browser impersonation and curl-cffi.
HTTP_PROXY=http://127.0.0.1:1082 HTTPS_PROXY=http://127.0.0.1:1082 ALL_PROXY=http://127.0.0.1:1082 \
env -u PYTHONHOME -u PYTHONPATH \
uvx --from yt-dlp --with bgutil-ytdlp-pot-provider --with curl-cffi yt-dlp \
  --impersonate safari \
  --remote-components ejs:github \
  --cookies-from-browser safari \
  --proxy http://127.0.0.1:1082 \
  --extractor-args 'youtube:player_client=mweb,web_creator' \
  -f 'bv*[height<=720][ext=mp4]+ba[ext=m4a]/bv*[height<=720]+ba/best[height<=720][ext=mp4]/18/best[height<=720]' \
  --merge-output-format mp4 \
  -o '/Volumes/SN770Coder/Data/video_reports/<id>/video/source_video.%(ext)s' \
  'YOUTUBE_URL'
```

Treat this as a validated pattern, not a permanent one-size-fits-all command. Adjust proxy/client/cookies based on live diagnostics.

## Temporary video policy

Default behavior for generic URL frame capture is temporary video download plus cleanup after frames are captured. If the user explicitly asks to retain the video or asks to prove downloadability, keep the video under the mounted-drive report directory and report its path.

## Visual evidence workflow

For full reports:

1. Build a visual manifest from transcript blocks.
2. Review whether the manifest selected enough frame windows for the video style.
3. Capture frames from the local MP4.
4. Run OCR on captured frames.
5. Run multimodal analysis when OCR is weak, or when the user specifically asks to extract information from images.
6. Compose the final report only after image-derived claims are backed by frame/OCR/vision outputs.
7. Inspect the final Markdown for internal pipeline language before rendering PDF.
