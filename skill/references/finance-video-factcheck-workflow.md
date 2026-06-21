# Finance / Economics Video Fact-Check Workflow

Use this reference when a user asks for a report on an economics, investing, market-structure, gambling/probability, or finance-adjacent video and asks to verify truthfulness.

## Goal

Produce a reader-facing report that separates:

- what the video claims;
- which claims are externally verifiable;
- which are commentary or rhetoric;
- what a beginner can safely learn;
- what remains unverified.

Do not treat the transcript summary or title as verified truth.

## Workflow

1. **Get spoken content first**
   - Prefer subtitles/captions.
   - If unavailable, download audio/video and run the configured Nemotron ASR backend.
   - If only title/metadata is available, produce only a metadata-limited report and say the substantive content is unverified.

2. **Inspect transcript directly**
   - Identify hard claims: dates, market sizes, percentages, formulas, named institutions, study names, regulatory rules, company/platform claims.
   - Identify soft claims: causal interpretations, political/economic judgments, rhetorical comparisons, broad social claims.

3. **Cross-check hard claims**
   - Use multiple source types where practical: official rules/filings, academic bibliographic pages, company primary/educational pages, reputable market research summaries.
   - For calculations, recompute with a local arithmetic tool and note discrepancies.
   - Downgrade claims when the number is plausible but not found in primary/official sources.

4. **Write a compact verification table**
   - Columns: video claim, verification result, confidence, explanation.
   - Suggested labels: `属实`, `基本属实`, `部分错误`, `部分可验证`, `无法确认`, `观点判断`.
   - Do not overstate commercial/company-authored sources; mark them as source-backed but potentially promotional.

5. **Add beginner-learning section**
   - Translate the video into transferable concepts, not trading instructions.
   - For finance-adjacent gambling videos, useful concepts include expected value, fees/overround, market price discovery, model assumptions, liquidity, and risk management.
   - Include a light risk boundary: research/education only, no trading/betting recommendation.

6. **Use domain skills when available**
   - If an investment/market-research skill is loaded, use its evidence ladder and risk boundary.
   - For Serenity-style research, map the system as scarce layers / value-chain constraints before discussing actors or platforms.

## YouTube bot-block audio-first path

When the normal `video_report.py URL` path fails with YouTube bot checks but browser cookies and `curl-cffi` can access formats, use an audio-first two-step:

```bash
mkdir -p /path/to/report
cd /path/to/report
HTTP_PROXY=http://127.0.0.1:1082 HTTPS_PROXY=http://127.0.0.1:1082 ALL_PROXY=http://127.0.0.1:1082 \
  env -u PYTHONHOME -u PYTHONPATH \
  uvx --from yt-dlp --with curl-cffi yt-dlp \
  --impersonate safari \
  --cookies-from-browser safari \
  --proxy http://127.0.0.1:1082 \
  -f '140/251/bestaudio[ext=m4a]/bestaudio' \
  -o 'source.%(ext)s' URL

VIDEO_REPORT_PYTHON=/path/to/.venv-nemotron/bin/python \
  env -u PYTHONHOME -u PYTHONPATH \
  /path/to/.venv-nemotron/bin/python SKILL_DIR/scripts/video_report.py \
  /path/to/report/source.m4a \
  --transcript-source asr \
  --asr-backend auto \
  --language zh-CN \
  --output-dir /path/to/report \
  --format both \
  --skip-summary \
  --keep-workdir \
  --chunk-seconds 60
```

Then generate a visual manifest / composer output if a full report is requested, but for finance/economics fact-checks, expect to write a polished verification report manually when the generic composer produces only a content reorganization.

## Pitfalls

- ASR can garble technical names, percentages, and formulas. Verify against the original math and external sources.
- Do not cite exact percentages from rhetoric unless independently sourced or recomputed.
- Do not infer video content from title, description, tags, recommendations, comments, or live chat.
- For lottery/gambling claims, distinguish official payout rules from author-estimated effective RTP for specific play styles.
