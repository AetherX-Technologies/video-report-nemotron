# Video Report Nemotron 中文文档

Video Report Nemotron 是一个 Hermes skill，用来把视频链接或本地音视频文件转换成结构化报告。它会优先使用平台已有字幕；只有在字幕不可用或你明确要求本机转录时，才会启用 Apple Silicon 本地 ASR，也就是 `mlx-community/nemotron-3.5-asr-streaming-0.6b-8bit`。

首页英文文档见 [README.md](../README.md)。

![报告渲染预览](assets/report-html-preview.png)

## 能做什么

- 支持 YouTube、Bilibili、其他 `yt-dlp` 支持的视频 URL，以及本地媒体文件。
- 默认优先读取已有字幕，避免不必要的本机转录。
- 字幕不可用时，用 MLX/Nemotron 在 Apple Silicon 上本地转录。
- 输出 Markdown 和 JSON，方便后续复用。
- 支持视觉报告链路：按转写时间块判断是否需要看视频，按需截图，先 OCR，再在 OCR 不足时使用多模态兜底。
- 最终生成面向用户的 Markdown、HTML、PDF 报告，支持表格、引用、列表、粗体、代码、图片和图片说明的正常渲染。

## 示例产物

仓库包含一个公开 YouTube 视频的示例最终报告：

- [Markdown 报告](../examples/QggkUtXNkPo/report.md)
- [HTML 报告](../examples/QggkUtXNkPo/report.html)
- [PDF 报告](../examples/QggkUtXNkPo/report.pdf)

报告只在内容确实需要视觉上下文的位置插入截图。

![视觉证据示例](assets/visual-evidence-frame.png)

## 目录结构

```text
.
├── skill/                     # Hermes skill 包
│   ├── SKILL.md               # skill 使用说明
│   ├── .env.example           # 运行配置模板，不包含真实 key
│   ├── scripts/               # 下载、转录、视觉、OCR、报告脚本
│   └── tests/                 # pytest 测试
├── docs/
│   ├── README.zh-CN.md        # 中文文档
│   └── assets/                # 文档截图
└── examples/
    └── QggkUtXNkPo/           # 示例最终报告
```

## 环境要求

这个 skill 主要面向 Apple Silicon macOS。

安装系统依赖：

```bash
brew install ffmpeg imagemagick
npm install -g @run-llama/liteparse
playwright install chromium
```

创建 Python 环境：

```bash
uv venv .venv-nemotron --python 3.12
uv pip install --python .venv-nemotron/bin/python \
  yt-dlp pytest "git+https://github.com/Blaizzy/mlx-audio.git"
```

默认 ASR 模型：

```text
mlx-community/nemotron-3.5-asr-streaming-0.6b-8bit
```

第一次启用本机 ASR 时，模型权重会通过 Hugging Face cache 下载。

## 配置

复制配置模板：

```bash
cp skill/.env.example skill/.env
```

`skill/.env.example` 里是 OpenAI-compatible 的文本生成和视觉兜底配置：

```dotenv
OPENAI_BASE_URL=https://sub2api.gptclubapi.xyz/v1
OPENAI_VISION_MODEL=gpt-5.5
OPENAI_TEXT_MODEL=gpt-5.5
OPENAI_API_KEY=
```

真实 API key 只放在本地 `.env` 或 shell 环境变量里，不要提交到 Git。

## 快速开始

生成一份以转写为核心的报告：

```bash
.venv-nemotron/bin/python skill/scripts/video_report.py \
  "https://www.youtube.com/watch?v=QggkUtXNkPo" \
  --language zh-CN \
  --output-dir reports/QggkUtXNkPo \
  --chunk-seconds 90
```

默认 `--transcript-source auto` 会先尝试平台字幕；字幕不可用时才启用本机 ASR。强制本机 ASR：

```bash
.venv-nemotron/bin/python skill/scripts/video_report.py ./video.mp4 \
  --transcript-source asr \
  --language zh-CN \
  --output-dir reports/local-video
```

## 视觉报告链路

`video_report.py` 生成 JSON 后，可以继续跑视觉报告：

```bash
# 1. 生成按时间块审核的视频截图 manifest。
.venv-nemotron/bin/python skill/scripts/video_visual_manifest.py \
  reports/QggkUtXNkPo/QggkUtXNkPo.json \
  -o reports/QggkUtXNkPo/visual/visual_manifest.json

# 2. 只对 needs_video=true 的时间块截图。
.venv-nemotron/bin/python skill/scripts/video_capture_frames.py \
  reports/QggkUtXNkPo/visual/visual_manifest.json \
  -o reports/QggkUtXNkPo/visual/visual_manifest.frames.json \
  --frames-dir reports/QggkUtXNkPo/visual/frames

# 3. 对截图跑 OCR。
.venv-nemotron/bin/python skill/scripts/video_ocr_frames.py \
  reports/QggkUtXNkPo/visual/visual_manifest.frames.json \
  -o reports/QggkUtXNkPo/visual/visual_manifest.ocr.json \
  --ocr-dir reports/QggkUtXNkPo/visual/ocr

# 4. 只在 OCR 不足时使用多模态兜底。
.venv-nemotron/bin/python skill/scripts/video_multimodal_frames.py \
  reports/QggkUtXNkPo/visual/visual_manifest.ocr.json \
  -o reports/QggkUtXNkPo/visual/visual_manifest.vision.json \
  --env-file skill/.env

# 5. 生成最终面向用户的报告。
.venv-nemotron/bin/python skill/scripts/video_compose_final_report.py \
  reports/QggkUtXNkPo/QggkUtXNkPo.json \
  reports/QggkUtXNkPo/visual/visual_manifest.vision.json \
  --markdown reports/QggkUtXNkPo/visual/report.md \
  --html reports/QggkUtXNkPo/visual/report.html \
  --pdf reports/QggkUtXNkPo/visual/report.pdf \
  --env-file skill/.env
```

## 安装为 Hermes Skill

复制到 Hermes skills 目录：

```bash
mkdir -p ~/.hermes/skills/media
rsync -a skill/ ~/.hermes/skills/media/video-report-nemotron/
```

之后就可以在 Hermes 里让它分析、转录、总结视频，或者生成完整 Markdown/HTML/PDF 报告。

## 测试

```bash
.venv-nemotron/bin/python -m pytest skill/tests
```

测试覆盖字幕优先策略、视觉 manifest、环境变量加载、报告生成和 PDF 所需的 Markdown 渲染。

## 安全说明

- 真实 `.env` 文件已在 `.gitignore` 中忽略。
- 下载的视频、音频和中间媒体文件不提交。
- 示例报告只保留小型可复现产物，不包含模型权重和原始媒体下载。
- 公开视频内容仍可能受平台条款和版权限制；请只处理你有权处理的内容。

