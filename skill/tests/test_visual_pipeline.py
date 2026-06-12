import importlib.util
import json
import sys
from pathlib import Path


def load_script(name):
    script_path = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


visual_manifest = load_script("video_visual_manifest")
visual_report = load_script("video_build_visual_report")
visual_multimodal = load_script("video_multimodal_frames")
final_report = load_script("video_compose_final_report")


def test_manifest_parses_timestamped_blocks_and_labels_visual_need():
    report = {
        "source": "https://example.com/video",
        "title": "Demo",
        "model": "model",
        "language": "zh-CN",
        "artifacts": {"json": "/tmp/report.json"},
        "transcript": (
            "[00:00-01:30] 这里介绍背景。\n\n"
            "[01:30-03:00] 这张支付矩阵展示五种状态，VIX 从 20 到 30，比例超过百分之四十。"
        ),
    }

    manifest = visual_manifest.build_manifest(report, mode="draft", max_frames=3)

    assert len(manifest["segments"]) == 2
    assert manifest["segments"][0]["needs_video"] is False
    assert manifest["segments"][1]["needs_video"] is True
    assert manifest["segments"][1]["sampling"]["times"]
    assert manifest["review_policy"]["keyword_triggered_capture"] is False


def test_build_visual_report_embeds_frames_and_ocr(tmp_path):
    frame = tmp_path / "assets" / "frame.png"
    frame.parent.mkdir()
    frame.write_bytes(b"not-a-real-image-for-markdown-test")
    manifest = {
        "source": "https://example.com/video",
        "title": "Demo",
        "segments": [
            {
                "id": "seg_0001",
                "timestamp": "01:30-03:00",
                "needs_video": True,
                "priority": "high",
                "rationale": "matrix and dense numbers",
                "evidence_types": ["matrix", "dense_numbers"],
                "visual_questions": ["画面是否展示矩阵？"],
                "transcript": "这张矩阵展示状态。",
                "frames": [
                    {
                        "path": str(frame),
                        "timestamp": "00:02:15",
                        "ocr": {
                            "status": "done",
                            "adequate": True,
                            "needs_multimodal": False,
                            "text": "矩阵 OCR",
                        },
                    }
                ],
            }
        ],
    }

    markdown = visual_report.build_markdown(manifest, tmp_path / "report.md", "Demo Report")

    assert "![seg_0001 00:02:15]" in markdown
    assert "矩阵 OCR" in markdown
    assert "Multimodal fallback" not in markdown


def test_build_html_renders_report_markdown_structures(tmp_path):
    markdown = """# 视频内容深度报告

> 原视频：https://example.com/video
> 说明：保留 **关键细节**。

## 五种状态

1. **震荡爆发期**
   系统总效应为负。
2. 多空绞杀期

| 参与者 | 效用 |
|---|---:|
| HFT | +13 |
| 散户 | -4 |

- VIX 突破 `40`
- 政策焦虑度超过 **300**

![图 1：状态矩阵](frames/state.png)

*图 1：状态矩阵。展示五种市场状态。*
"""

    rendered = visual_report.build_html(markdown, {}, tmp_path / "report.html", "Demo")

    assert "<blockquote>" in rendered
    assert "<strong>关键细节</strong>" in rendered
    assert "<ol>" in rendered
    assert "<li><strong>震荡爆发期</strong>" in rendered
    assert "<ul>" in rendered
    assert "<code>40</code>" in rendered
    assert "<table>" in rendered
    assert '<td style="text-align: right">+13</td>' in rendered
    assert "<figcaption>" in rendered
    assert "图 1：状态矩阵。展示五种市场状态。" in rendered
    assert "&gt; 原视频" not in rendered
    assert "| 参与者 | 效用 |" not in rendered
    assert "1. **震荡爆发期**" not in rendered


def test_final_visual_report_is_user_facing_and_selective(tmp_path):
    frame = tmp_path / "assets" / "frame.png"
    frame.parent.mkdir()
    frame.write_bytes(b"not-a-real-image-for-markdown-test")
    manifest = {
        "source": "https://example.com/video",
        "title": "Demo",
        "segments": [
            {
                "id": "seg_0001",
                "timestamp": "01:30-03:00",
                "needs_video": True,
                "priority": "high",
                "rationale": "matrix and dense numbers",
                "evidence_types": ["matrix", "dense_numbers"],
                "transcript": "这张支付矩阵展示市场状态。关键结论是识别状态比预测价格重要。",
                "frames": [
                    {
                        "path": str(frame),
                        "timestamp": "00:02:15",
                        "ocr": {
                            "status": "done",
                            "adequate": True,
                            "needs_multimodal": False,
                            "text": "大量 OCR 原文不应该整段进入最终报告",
                        },
                    }
                ],
            }
        ],
    }

    markdown = visual_report.build_final_markdown(manifest, tmp_path / "final.md", "Final Report", 3)

    assert "## 核心结论" in markdown
    assert "## 关键图像证据" in markdown
    assert "![seg_0001 00:02:15]" in markdown
    assert "**OCR text**" not in markdown
    assert "大量 OCR 原文不应该整段进入最终报告" not in markdown


def test_compose_report_replaces_image_placeholders(tmp_path):
    frame = tmp_path / "assets" / "frame.png"
    frame.parent.mkdir()
    frame.write_bytes(b"not-a-real-image-for-markdown-test")
    image_bank = [
        {
            "id": "img_01",
            "path": "assets/frame.png",
            "caption": "矩阵图",
            "note": "显示市场状态矩阵。",
        }
    ]

    markdown = final_report.render_images("正文\n\n[IMAGE:img_01]\n\n解释", image_bank)

    assert "![矩阵图](assets/frame.png)" in markdown
    assert "[IMAGE:img_01]" not in markdown


def test_validate_final_report_rejects_short_or_unresolved_output():
    image_bank = [{"id": "img_01", "path": "frame.png", "caption": "图", "note": "说明"}]

    try:
        final_report.validate_final_report("# 标题\n\n## 核心结论\n\n太短\n\n[IMAGE:img_01]\n\n## 读者使用提醒\n\n提醒", image_bank, "zh-CN")
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("validate_final_report should reject weak reports")

    assert "too short" in message
    assert "unresolved image placeholders" in message


def test_ensure_standard_sections_wraps_llm_markdown():
    markdown = "# Demo\n\n## 自定义章节\n\n" + ("这是模型生成的正文。" * 120)

    wrapped = final_report.ensure_standard_sections(markdown, "zh-CN")

    assert "## 核心结论" in wrapped
    assert "## 读者使用提醒" in wrapped
    assert "## 自定义章节" in wrapped


def test_compose_infers_report_language_from_transcript():
    zh_report = {"language": "", "transcript": "这里是中文视频内容，讨论市场状态和风险信号。" * 5}
    en_report = {"language": "", "transcript": "This is an English lecture about markets, signals, and risk." * 5}

    assert final_report.infer_report_language(zh_report, "auto") == "zh-CN"
    assert final_report.infer_report_language(en_report, "auto") == "en"
    assert final_report.infer_report_language(en_report, "zh-CN") == "zh-CN"


def test_compose_deterministic_report_can_render_english_fallback(tmp_path):
    frame = tmp_path / "assets" / "frame.png"
    frame.parent.mkdir()
    frame.write_bytes(b"not-a-real-image-for-markdown-test")
    report = {
        "source": "https://example.com/video",
        "title": "Demo",
        "language": "en",
        "transcript": (
            "[00:00-01:30] This lecture explains market states, VIX 40, "
            "liquidity risk, spoofing, and why signal quality matters."
        ),
    }
    image_bank = [
        {
            "id": "img_01",
            "path": "assets/frame.png",
            "caption": "Market state diagram",
            "note": "Shows the state matrix.",
            "segment_text": "market states",
            "start": "0",
            "timestamp": "00:00-01:30",
        }
    ]

    markdown = final_report.deterministic_report(report, image_bank, "Demo Report", "en")

    assert "## Core Takeaways" in markdown
    assert "## Reader Notes" in markdown
    assert "Source video:" in markdown
    assert "原视频" not in markdown
    assert "## 核心结论" not in markdown
    assert "[IMAGE:img_01]" in markdown


def test_validate_final_report_rejects_wrong_output_language():
    image_bank = []
    markdown = (
        "# 穷鬼移民 Deep Report\n\n"
        "## Core Takeaways\n\n"
        + ("这是一段中文内容，应该不能作为英文最终报告通过。" * 80)
        + "\n\n## Reader Notes\n\n"
        "Treat this report as a checklist."
    )

    try:
        final_report.validate_final_report(markdown, image_bank, "en")
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("validate_final_report should reject wrong-language output")

    assert "too much Chinese" in message or "title is not" in message


def test_compose_image_bank_localizes_english_captions(tmp_path):
    frame = tmp_path / "frames" / "frame.png"
    frame.parent.mkdir()
    frame.write_bytes(b"not-a-real-image-for-markdown-test")
    manifest = {
        "segments": [
            {
                "id": "seg_0001",
                "timestamp": "00:00-01:30",
                "needs_video": True,
                "priority": "medium",
                "evidence_types": ["chart"],
                "transcript": "The speaker is showing a chart of core AI skills.",
                "frames": [
                    {
                        "path": str(frame),
                        "timestamp": "00:00:12",
                        "ocr": {"status": "done", "adequate": True, "text": "AI Skills"},
                    }
                ],
            }
        ]
    }

    image_bank = final_report.build_image_bank(manifest, tmp_path / "report.md", 3, "en")
    markdown = final_report.render_images("[IMAGE:img_01]\n", image_bank, "en")

    assert image_bank[0]["caption"] == "Figure 1: Chart or metric view"
    assert "This image shows chart or metric view" in image_bank[0]["note"]
    assert "![Figure 1: Chart or metric view]" in markdown
    assert "图 1" not in markdown
    assert "曲线或指标图" not in markdown
    assert not any("\u4e00" <= char <= "\u9fff" for char in markdown)


def test_compose_fallback_report_is_detail_preserving_and_user_facing(tmp_path):
    frame = tmp_path / "assets" / "frame.png"
    frame.parent.mkdir()
    frame.write_bytes(b"not-a-real-image-for-markdown-test")
    report = {
        "source": "https://example.com/video",
        "title": "Demo",
        "transcript": (
            "[00:00-01:30] 支付矩阵 博弈 VIX 期权 NLP 情绪 政策 新颖度 "
            "订单 撤单 挂单 spoofing 闪电崩盘 GameStop Benford 整数价位 稳定币 "
            "热点传播 收益窗口。这里还有 2020 年 3 月 16 日 VIX 82.69、"
            "情绪分 -0.5、政策焦虑度 200 和 300、撤单量是成交量 50 倍以上等细节。"
        ),
        "summary": "这期视频讨论市场状态识别。",
        "key_points": [],
    }
    image_bank = [
        {
            "id": "img_01",
            "path": "assets/frame.png",
            "caption": "市场状态支付矩阵",
            "note": "展示支付矩阵。",
            "segment_text": "支付矩阵 博弈",
            "evidence_types": "matrix",
            "start": "0",
            "timestamp": "00:00-01:30",
        }
    ]

    markdown = final_report.deterministic_report(report, image_bank, "视频内容深度报告")
    markdown = final_report.clean_final_markdown(final_report.render_images(markdown, image_bank, "zh-CN"))

    assert len(final_report.plain_text(markdown)) > 1200
    assert "## 核心结论" in markdown
    assert "## 读者使用提醒" in markdown
    assert "2020 年 3 月 16 日" in markdown
    assert "VIX 82.69" in markdown
    assert "撤单量是成交量 50 倍以上" in markdown
    assert "![市场状态支付矩阵](assets/frame.png)" in markdown
    assert "[IMAGE:img_01]" not in markdown
    assert "OCR" not in markdown
    assert "manifest" not in markdown
    assert "Transcript block" not in markdown
    assert "需要配图" not in markdown
    assert "适合插入" not in markdown
    final_report.validate_final_report(markdown, image_bank, "zh-CN")


def test_compose_selects_frames_across_whole_video(tmp_path):
    segments = []
    for index in range(12):
        frame = tmp_path / f"frame_{index}.png"
        frame.write_bytes(b"not-a-real-image-for-markdown-test")
        segments.append(
            {
                "id": f"seg_{index:04d}",
                "timestamp": f"{index:02d}:00-{index + 1:02d}:00",
                "needs_video": True,
                "priority": "medium",
                "evidence_types": ["chart"],
                "transcript": f"第 {index} 段 VIX 期权 状态切换 热点传播",
                "frames": [
                    {
                        "path": str(frame),
                        "timestamp": f"00:{index:02d}:10",
                        "ocr": {"status": "done", "adequate": True, "text": "chart"},
                    }
                ],
            }
        )
    manifest = {"segments": segments}

    selected = final_report.select_report_frames(manifest, 5)

    assert selected[0][0]["id"] == "seg_0000"
    assert selected[-1][0]["id"] == "seg_0011"
    assert len(selected) == 5


def test_multimodal_only_targets_weak_ocr_frames():
    weak = {"ocr": {"needs_multimodal": True, "status": "done"}}
    strong = {"ocr": {"needs_multimodal": False, "status": "done"}}
    error = {"ocr": {"status": "error"}}

    assert visual_multimodal.should_analyze(weak, include_all=False) is True
    assert visual_multimodal.should_analyze(strong, include_all=False) is False
    assert visual_multimodal.should_analyze(error, include_all=False) is True
    assert visual_multimodal.should_analyze(strong, include_all=True) is True


def test_multimodal_loads_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "OPENAI_BASE_URL=https://example.test/v1",
                "OPENAI_VISION_MODEL='vision-test'",
                "OPENAI_API_KEY=",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_VISION_MODEL", raising=False)

    loaded = visual_multimodal.load_env_file(env_file)

    assert loaded is True
    assert visual_multimodal.os.environ["OPENAI_BASE_URL"] == "https://example.test/v1"
    assert visual_multimodal.os.environ["OPENAI_VISION_MODEL"] == "vision-test"


def test_build_visual_report_includes_multimodal_text(tmp_path):
    frame = tmp_path / "assets" / "frame.png"
    frame.parent.mkdir()
    frame.write_bytes(b"not-a-real-image-for-markdown-test")
    manifest = {
        "source": "https://example.com/video",
        "title": "Demo",
        "segments": [
            {
                "id": "seg_0001",
                "timestamp": "01:30-03:00",
                "needs_video": True,
                "priority": "high",
                "rationale": "weak OCR",
                "evidence_types": ["chart"],
                "visual_questions": [],
                "transcript": "这条曲线说明收益窗口衰减。",
                "frames": [
                    {
                        "path": str(frame),
                        "timestamp": "00:02:15",
                        "ocr": {
                            "status": "done",
                            "adequate": False,
                            "needs_multimodal": True,
                            "text": "少量 OCR",
                        },
                        "vision": {
                            "status": "done",
                            "provider": "openai",
                            "model": "vision-model",
                            "text": "图中是一条衰减曲线。",
                        },
                    }
                ],
            }
        ],
    }

    markdown = visual_report.build_markdown(manifest, tmp_path / "report.md", "Demo Report")

    assert "**Multimodal analysis**" in markdown
    assert "图中是一条衰减曲线。" in markdown
