# Bilibili 412 / Precondition Failed Capture Notes

Use this reference when `video_report.py` or `yt-dlp` fails on a Bilibili URL with `HTTP Error 412: Precondition Failed`, or when browser navigation shows Bilibili `错误号: 412`.

## Symptom

- `yt-dlp` fails before video metadata extraction:
  - `ERROR: [BiliBili] ... Unable to download webpage: HTTP Error 412: Precondition Failed`
- Browser access may show Bilibili error page with `错误号: 412`.
- Adding common headers (`User-Agent`, `Referer`, `Accept-Language`) may not be enough.

## Meaning

This is a Bilibili access/risk-control block at the webpage/API layer, not an ASR failure and not a normal missing-subtitle case. The pipeline cannot proceed until a real accessible media source is provided.

## Recovery Order

1. Try `yt-dlp` with a real logged-in cookie source or cookie file:
   - `yt-dlp --cookies-from-browser chrome URL ...`
   - or `yt-dlp --cookies /path/to/cookies.txt URL ...`
2. If cookie access is unavailable, ask the user for a local MP4/audio file path downloaded from a browser/session that can access the video.
3. Once a local file exists, run the same video-report pipeline using the local media path as `source` and pass the local file again to frame capture via `--video-source /absolute/video.mp4`.

## Do Not

- Do not treat 412 as a transcript/subtitle absence.
- Do not replace Nemotron ASR with Whisper-family fallbacks.
- Do not claim Bilibili cannot work generally; this is an access-context issue that cookies or a local media file can usually resolve.
