import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "video_report.py"
SPEC = importlib.util.spec_from_file_location("video_report", SCRIPT_PATH)
video_report = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules["video_report"] = video_report
SPEC.loader.exec_module(video_report)


def test_write_outputs_records_artifact_paths(tmp_path):
    result = video_report.ProcessingResult(
        source="local.wav",
        title="Demo Report",
        model=video_report.DEFAULT_MODEL,
        language="zh-CN",
        created_at="2026-01-01T00:00:00+00:00",
        transcript="第一段内容。下一步需要整理结论。",
        summary="第一段内容。",
        key_points=["第一段内容。"],
        action_items=["下一步需要整理结论。"],
        artifacts=video_report.ArtifactPaths(
            output_dir=str(tmp_path),
            markdown=None,
            json=None,
            audio=None,
        ),
    )

    written = video_report.write_outputs(result, tmp_path, "both")

    markdown_path = tmp_path / "Demo-Report.md"
    json_path = tmp_path / "Demo-Report.json"
    assert markdown_path.exists()
    assert json_path.exists()
    assert written.artifacts.markdown == str(markdown_path)
    assert written.artifacts.json == str(json_path)
    assert "# Demo Report" in markdown_path.read_text(encoding="utf-8")
    assert '"markdown":' in json_path.read_text(encoding="utf-8")


def test_parse_vtt_cues_and_group_subtitle_transcript(tmp_path):
    vtt = tmp_path / "captions.vtt"
    vtt.write_text(
        """WEBVTT

00:00:00.000 --> 00:00:02.000
第一句字幕

00:00:03.000 --> 00:00:05.000
第二句字幕

00:01:35.000 --> 00:01:36.000
第二段字幕
""",
        encoding="utf-8",
    )

    cues = video_report.parse_vtt_cues(vtt)
    transcript = video_report.cues_to_timestamped_transcript(cues, 90)

    assert cues[0] == (0.0, 2.0, "第一句字幕")
    assert "[00:00-01:30] 第一句字幕 第二句字幕" in transcript
    assert "[01:30-03:00] 第二段字幕" in transcript
