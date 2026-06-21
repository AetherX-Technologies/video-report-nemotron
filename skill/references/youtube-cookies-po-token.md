# YouTube Cookies, Captions, PO Token, and ASR Fallback

Use this reference when a YouTube video report needs real spoken content but normal subtitle/media extraction fails.

## Durable Diagnostic Pattern

1. First try subtitles/captions with cookies when available:

```bash
yt-dlp --cookies-from-browser safari --no-playlist --list-subs --ignore-no-formats URL
```

2. Interpret results carefully:

- `Extracted N cookies from safari` means browser cookie access works.
- `Operation not permitted: ... Cookies.binarycookies` means macOS Full Disk Access/TCC blocks cookie reading. Fix permissions and restart the Hermes/terminal process before retrying.
- `has no automatic captions` plus only `live_chat` means the video has no usable speech transcript from YouTube. Do not summarize spoken content from metadata/title.
- `Only images are available for download` and formats limited to `sb*` storyboard means `yt-dlp` did not receive audio/video formats.

3. Dump metadata to distinguish public metadata access from playable media access:

```bash
yt-dlp --cookies cookies.txt --ignore-no-formats --dump-single-json --no-playlist URL > info.json 2> info.err
```

Useful fields: `live_status`, `availability`, `duration`, `formats`, `subtitles`, `automatic_captions`.

## PO Token / Player Challenge Symptoms

YouTube may expose metadata and cookies but still withhold media formats. Common symptoms:

- `n challenge solving failed`
- player JS download repeatedly ends with `IncompleteRead`
- `formats` contains only `sb0`, `sb1`, etc.
- no `audio`/`video` formats even after trying `player_client` variants

Try, but do not loop indefinitely:

```bash
yt-dlp --cookies cookies.txt --remote-components ejs:github --list-formats URL
yt-dlp --cookies cookies.txt --extractor-args 'youtube:formats=missing_pot' --list-formats URL
yt-dlp --cookies cookies.txt --extractor-args 'youtube:player_client=web,web_safari,mweb,tv,web_creator' --list-formats URL
```

If these still yield only storyboard formats, treat the media extraction path as blocked by YouTube playback integrity/PO-token requirements and switch to a user-assisted content source.

## Valid Fallbacks

Prefer these, in order:

1. User provides transcript copied from the page.
2. User provides local audio/video file.
3. User exports browser cookies to `cookies.txt` and `yt-dlp --cookies cookies.txt` works.
4. If the user explicitly consents, ask them to play the video in the browser and record system audio for ASR.

For recording fallback, first list devices:

```bash
ffmpeg -hide_banner -f avfoundation -list_devices true -i ''
```

Only record after explicit user consent, because it captures current computer audio. Then run the skill's normal ASR/report pipeline on the local audio file.

## Reporting Rule

When only metadata/live chat is available, report that spoken content was not obtained. Do not generate a content report from the title, description, recommendations, ads, or live chat alone unless the user explicitly asks for a metadata-only report.
