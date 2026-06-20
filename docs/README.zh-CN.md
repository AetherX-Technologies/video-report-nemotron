# Video Report Nemotron 中文文档

Video Report Nemotron 是一个 Hermes skill，用来把视频链接或本地音视频文件转换成结构清晰、可直接给用户看的 Markdown、HTML、PDF 图文报告。它默认优先使用平台已有字幕；只有字幕不可用或你明确要求本机转录时，才启用 Nemotron ASR。

首页英文文档见 [README.md](../README.md)。

![报告渲染预览](assets/report-html-preview.png)

## 核心能力

- 支持 YouTube、本地音视频文件，以及其他在当前环境里能正常获取字幕或媒体的 `yt-dlp` 支持 URL。
- 默认优先读取已有字幕，避免不必要的本机转录。
- 本机 ASR 只使用 Nemotron。当前仓库自带的本地后端是 Apple Silicon MLX：`mlx-community/nemotron-3.5-asr-streaming-0.6b-8bit`。
- 不会偷偷回退到 Whisper / faster-whisper。Linux 或 Windows 可以先走字幕优先链路；如果要本地 ASR，需要接入明确的 Nemotron 后端。
- 支持视觉报告链路：按转写时间块判断是否需要看视频，按需截图，先 OCR，再在 OCR 不足时使用多模态兜底。
- 最终生成面向用户的 Markdown、HTML、PDF 报告，图片会插入到真正相关的正文位置。
- 可以强制指定最终报告语言，例如英文视频输出中文报告，中文视频输出英文报告。
- 可用于 Hermes CLI，也可用于 Hermes Desktop。

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

通用依赖：

```bash
brew install ffmpeg imagemagick
npm install -g @run-llama/liteparse
playwright install chromium
```

Python 环境：

```bash
uv venv .venv-nemotron --python 3.12
uv pip install --python .venv-nemotron/bin/python yt-dlp httpx pytest
```

Apple Silicon 本机 ASR：

```bash
uv pip install --python .venv-nemotron/bin/python \
  "git+https://github.com/Blaizzy/mlx-audio.git"
```

默认 ASR 模型：

```text
mlx-community/nemotron-3.5-asr-streaming-0.6b-8bit
```

第一次启用本机 ASR 时，模型权重会通过 Hugging Face cache 下载。Linux / Windows 可以正常使用字幕优先的视频报告；本机 ASR 需要先接入明确的 Nemotron 后端，不能用 Whisper 系列模型替代。

## 配置

复制配置模板：

```bash
cp skill/.env.example skill/.env
```

`skill/.env.example` 里是 OpenAI-compatible 的报告生成和视觉兜底配置：

```dotenv
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_TEXT_MODEL=gpt-4.1-mini
OPENAI_VISION_MODEL=gpt-4.1-mini
OPENAI_API_KEY=
REPORT_LANGUAGE=auto
ASR_BACKEND=auto
MLX_ASR_MODEL=mlx-community/nemotron-3.5-asr-streaming-0.6b-8bit
```

你可以替换为任何 OpenAI-compatible API endpoint 和模型名。真实 API key 只放在本地 `.env` 或 shell 环境变量里，不要提交到 Git。

## 快速开始

先生成转写和元数据中间产物：

```bash
.venv-nemotron/bin/python skill/scripts/video_report.py \
  "https://www.youtube.com/watch?v=QggkUtXNkPo" \
  --transcript-source auto \
  --asr-backend auto \
  --language zh-CN \
  --output-dir reports/QggkUtXNkPo \
  --chunk-seconds 90
```

默认 `--transcript-source auto` 会先尝试平台字幕；字幕不可用时才会提取音频并使用选定的 Nemotron 后端。

## YouTube 新访问策略

这一节只针对 YouTube，不是本地文件、Bilibili 或其他 `yt-dlp` 来源的通用策略。

YouTube 现在更频繁地对匿名抓取触发 bot 校验、播放完整性校验或 PO-token/player challenge。典型现象包括：`Sign in to confirm you're not a bot`、反复下载 player JavaScript 时出现 `IncompleteRead`、`n challenge solving failed`，或者格式列表里只有 `sb0`/`sb1` 这类 storyboard 条目。遇到这些情况时，不要把标题、简介、推荐、广告或 live chat 当成视频口播正文来写报告。

推荐顺序：

1. 先尝试字幕/自动字幕；如果视频需要登录态，就使用浏览器 cookies 或导出的 `cookies.txt` 做诊断：

```bash
yt-dlp --cookies-from-browser safari --no-playlist --list-subs --ignore-no-formats URL
yt-dlp --cookies cookies.txt --no-playlist --list-subs --ignore-no-formats URL
```

2. 如果 cookies 可用但视频没有字幕，优先让用户提供本地音视频文件，然后走正常 ASR 链路：

```bash
.venv-nemotron/bin/python skill/scripts/video_report.py ./video.mp4 \
--transcript-source asr \
--asr-backend auto \
--language en-US \
--output-dir reports/local-video
```

3. 如果 YouTube 只开放元数据、不开放可播放媒体格式，可以少量尝试 `yt-dlp` 的 player/client 诊断参数，然后停止；不要无限循环尝试随机 extractor 设置。
4. 如果最终仍然只有元数据或 storyboard 格式，就要求用户提供复制出来的字幕、本地媒体文件，或可用的 `cookies.txt`。只有在用户明确要求时，才生成 metadata-only 报告。

更详细的 YouTube 诊断参考见 [skill/references/youtube-cookies-po-token.md](../skill/references/youtube-cookies-po-token.md)。

强制本机 ASR：

```bash
.venv-nemotron/bin/python skill/scripts/video_report.py ./video.mp4 \
  --transcript-source asr \
  --asr-backend auto \
  --language en-US \
  --output-dir reports/local-video
```

## 最终报告链路

`video_report.py` 的 Markdown 是中间转写产物，不是最终面向用户的报告。完整报告要继续跑视觉链路和最终 composer：

```bash
# 1. 审核每个转写时间块，判断是否需要视频画面。
.venv-nemotron/bin/python skill/scripts/video_visual_manifest.py \
  reports/QggkUtXNkPo/QggkUtXNkPo.json \
  -o reports/QggkUtXNkPo/visual/visual_manifest.json

# 2. 只对审核后需要画面的时间块截图。
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
  --env-file skill/.env \
  --analysis-language auto

# 5. 生成最终面向用户的 Markdown、HTML、PDF。
.venv-nemotron/bin/python skill/scripts/video_compose_final_report.py \
  reports/QggkUtXNkPo/QggkUtXNkPo.json \
  reports/QggkUtXNkPo/visual/visual_manifest.vision.json \
  --markdown reports/QggkUtXNkPo/visual/report.md \
  --html reports/QggkUtXNkPo/visual/report.html \
  --pdf reports/QggkUtXNkPo/visual/report.pdf \
  --env-file skill/.env \
  --report-language zh-CN
```

用 `--report-language en`、`--report-language zh-CN` 或其他语言标签可以强制最终报告语言。composer 会检查明显的语言错误；如果模型没有按要求输出，会自动重写一次。

## Hermes CLI

安装 skill：

```bash
mkdir -p ~/.hermes/skills/media
rsync -a skill/ ~/.hermes/skills/media/video-report-nemotron/
```

然后在 Hermes 里直接要求生成最终报告：

```text
使用 video-report-nemotron 分析 https://www.youtube.com/watch?v=QggkUtXNkPo。
强制输出简体中文，生成最终 Markdown、HTML、PDF 报告。
```

## Hermes Desktop

Hermes Desktop 也能用。关键是把 skill 安装到 Desktop backend 当前使用的 `HERMES_HOME` 下面。

默认本地目录：

```bash
mkdir -p ~/.hermes/skills/media
rsync -a skill/ ~/.hermes/skills/media/video-report-nemotron/
```

如果你是从 Hermes 源码仓库启动 Desktop，并且设置了其他 `HERMES_HOME`，就安装到那个 home 里。安装后在 Desktop 里执行 `/reload-skills`，或者重启桌面端。

桌面端提示词示例：

```text
使用 video-report-nemotron 分析这个视频：
https://www.youtube.com/watch?v=QggkUtXNkPo

生成内容丰富、配图合理的最终报告。
输出 Markdown、HTML、PDF。
强制报告语言为英文。
```

## 测试

```bash
.venv-nemotron/bin/python -m pytest skill/tests
```

测试覆盖字幕优先策略、仅 Nemotron 的 ASR 后端选择、强制输出语言、视觉 manifest、环境变量加载、报告生成和 PDF 所需的 Markdown 渲染。

## 安全说明

- 真实 `.env` 文件已在 `.gitignore` 中忽略。
- 下载的视频、音频和中间媒体文件不提交。
- 示例报告只保留小型可复现产物，不包含模型权重和原始媒体下载。
- 公开视频内容仍可能受平台条款和版权限制；请只处理你有权处理的内容。
