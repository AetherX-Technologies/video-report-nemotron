# Report Quality Pitfalls

Use this reference before sending a user-facing video report PDF.

## Legibility and rendering

Do not hand-roll Markdown-to-HTML/PDF with simple line splitting. It loses tables, emphasis, lists, image layout, and code blocks.

Use one of the skill renderers:

- `video_compose_final_report.py` for composed video reports;
- `video_build_visual_report.py` for visual audit/evidence reports;
- `video_render_markdown.py` for manually written fact-check addenda or existing Markdown.

Before sending, inspect rendered HTML/PDF enough to verify:

- tables render as tables when present;
- `**bold**` renders as bold;
- images display from relative paths;
- no raw Markdown syntax is visibly broken;
- file is non-empty and recognized as PDF.

## Internal-language cleanup

Search the final Markdown for pipeline terms and remove them from user-facing prose/captions:

```text
manifest
OCR
ASR
视觉证据
多模态
需要配图
适合插入
适合放入
证据目录
score=
digit_density
建议不嵌入
```

It is fine for files and internal manifests to use those terms. The final report should explain what the viewer can learn, not expose pipeline mechanics.

## Finance/economics report checks

When a video is economics, investing, gambling/probability, market-structure, or finance-adjacent:

1. Separate hard claims from interpretation.
2. Recompute formulas and percentages mentioned in the transcript.
3. Cross-check hard claims against official, academic, or primary sources where feasible.
4. Mark uncertain numbers clearly instead of smoothing them into the narrative.
5. Keep user-facing language research-oriented, not trade-action advice.

## Visual evidence standard

A report may include image-derived claims only if frames were captured and OCR/vision was run. If the source is audio-only or media formats are blocked, label the report as text-only.
