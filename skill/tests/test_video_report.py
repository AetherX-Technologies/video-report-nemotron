import importlib.util
import os
import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest


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


def test_default_subtitle_languages_are_global_first():
    assert video_report.default_subtitle_languages(None).startswith("en.*,en,zh.*")
    assert video_report.default_subtitle_languages("zh-CN").startswith("zh.*")
    assert video_report.default_subtitle_languages("ja-JP") == "ja-JP,ja.*,en.*,zh.*"


def test_source_title_prefers_video_metadata_for_urls(monkeypatch):
    def fake_run_optional_command(command, *, cwd=None):
        assert command[:4] == ["yt-dlp", "--no-playlist", "--no-warnings", "--print"]
        return CompletedProcess(command, 0, stdout="Updated Essential AI Skills For 2026\n", stderr="")

    monkeypatch.setattr(video_report, "run_optional_command", fake_run_optional_command)

    assert video_report.source_title("https://www.youtube.com/watch?v=tu4rU4YD1Jk", None) == "Updated Essential AI Skills For 2026"


def test_source_title_falls_back_when_metadata_unavailable(monkeypatch):
    def fake_run_optional_command(command, *, cwd=None):
        return CompletedProcess(command, 1, stdout="", stderr="failed")

    monkeypatch.setattr(video_report, "run_optional_command", fake_run_optional_command)

    assert video_report.source_title("https://www.youtube.com/watch?v=tu4rU4YD1Jk", None) == "www.youtube.com - watch"


def test_backend_specific_default_models(monkeypatch):
    monkeypatch.setenv("MLX_ASR_MODEL", "custom-mlx-model")

    assert video_report.default_asr_model("mlx-nemotron") == "custom-mlx-model"
    with pytest.raises(RuntimeError, match="Unsupported ASR backend"):
        video_report.default_asr_model("other")


def test_model_override_applies_to_mlx_nemotron(monkeypatch):
    monkeypatch.setenv("MLX_ASR_MODEL", "default-mlx-model")

    assert video_report.resolve_model_name("mlx-nemotron", None) == "default-mlx-model"
    assert video_report.resolve_model_name("mlx-nemotron", "custom-model") == "custom-model"


def test_resolve_asr_backend_does_not_fallback_to_whisper_or_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(video_report.shutil, "which", lambda _name: None)
    monkeypatch.setattr(video_report, "host_prefers_mlx", lambda: False)
    monkeypatch.setattr(video_report, "has_mlx_audio", lambda: False)

    with pytest.raises(RuntimeError, match="No Nemotron ASR backend"):
        video_report.resolve_asr_backend("auto")


def test_resolve_asr_backend_prefers_local_mlx_on_apple_silicon(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(video_report.shutil, "which", lambda _name: "/usr/local/bin/whisper-cli")
    monkeypatch.setattr(video_report, "host_prefers_mlx", lambda: True)
    monkeypatch.setattr(video_report, "has_mlx_audio", lambda: True)

    assert video_report.resolve_asr_backend("auto") == "mlx-nemotron"


def test_resolve_asr_backend_ignores_whisper_cli(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(video_report, "host_prefers_mlx", lambda: False)
    monkeypatch.setattr(video_report, "has_mlx_audio", lambda: False)
    monkeypatch.setattr(video_report.shutil, "which", lambda name: "/usr/local/bin/whisper-cli" if name == "whisper-cli" else None)

    with pytest.raises(RuntimeError, match="No Nemotron ASR backend"):
        video_report.resolve_asr_backend("auto")


def test_requested_asr_backend_from_argv_uses_env_default(monkeypatch):
    monkeypatch.setenv("ASR_BACKEND", "mlx-nemotron")

    assert video_report.requested_asr_backend_from_argv(["input.mp4"]) == "mlx-nemotron"
    assert video_report.requested_asr_backend_from_argv(["input.mp4", "--asr-backend", "auto"]) == "auto"


def test_candidate_python_executables_include_nearby_project_venvs(monkeypatch, tmp_path):
    script_root = tmp_path / "skill"
    script_path = script_root / "scripts" / "video_report.py"
    script_path.parent.mkdir(parents=True)
    script_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(video_report, "__file__", str(script_path))
    monkeypatch.chdir(tmp_path)

    candidates = video_report.candidate_python_executables()

    assert tmp_path / ".venv-nemotron" / "bin" / "python" in candidates
    assert script_root / ".venv-nemotron" / "bin" / "python" in candidates


def test_parse_args_loads_env_file_defaults(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "ASR_BACKEND=mlx-nemotron",
                "OPENAI_BASE_URL=https://example.invalid/v1",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("ASR_BACKEND", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("VIDEO_REPORT_REEXECED_FOR_MLX", "1")
    monkeypatch.setattr(sys, "argv", ["video_report.py", "input.mp4", "--env-file", str(env_file)])

    args = video_report.parse_args()

    assert args.asr_backend == "mlx-nemotron"
    assert os.environ["OPENAI_BASE_URL"] == "https://example.invalid/v1"
