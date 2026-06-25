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
- 对 URL 来源抽帧时，先下载稳定的本地视频副本，再交给 `ffmpeg` 截图；默认抽帧完成后删除临时视频，除非显式使用 `--keep-video`。
- 新增 `video_render_markdown.py`，用于把已有 Markdown 报告可靠渲染成 HTML/PDF，并保留表格、图片、粗体和 inline code。
- 最终生成面向用户的 Markdown、HTML、PDF 报告，图片会插入到真正相关的正文位置。
- 可以强制指定最终报告语言，例如英文视频输出中文报告，中文视频输出英文报告。
- 可用于 Hermes CLI，也可用于 Hermes Desktop。

## 示例输出

仓库里包含一个公开视频的示例报告：

- [Markdown report](../examples/QggkUtXNkPo/report.md)
- [HTML report](../examples/QggkUtXNkPo/report.html)
- [PDF report](../examples/QggkUtXNkPo/report.pdf)

报告只在视觉信息真正支持正文分析的位置嵌入截图。

![视觉证据截图](assets/visual-evidence-frame.png)

## 仓库结构

```text
.
├── skill/                      # Hermes skill 包
│   ├── SKILL.md                # skill 使用说明
│   ├── .env.example            # 运行配置模板，不包含真实 key
│   ├── references/             # YouTube/Bilibili/报告质量诊断参考
│   ├── scripts/                # 下载、转写、视觉、OCR、渲染脚本
│   └── tests/                  # pytest 测试
├── docs/
│   ├── README.zh-CN.md         # 中文文档
│   └── assets/                 # 文档截图
└── examples/
    └── QggkUtXNkPo/            # 示例最终报告
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

## 从 GitHub 安装

克隆公开仓库并创建本地运行环境：

```bash
git clone git@github.com:AetherX-Technologies/video-report-nemotron.git
cd video-report-nemotron

uv venv .venv-nemotron --python 3.12
uv pip install --python .venv-nemotron/bin/python yt-dlp httpx pytest
uv pip install --python .venv-nemotron/bin/python \
  "git+https://github.com/Blaizzy/mlx-audio.git"
```

安装到 Hermes：

```bash
mkdir -p ~/.hermes/skills/media
rsync -a --delete \
  --exclude '.env' \
  --exclude '__pycache__' \
  --exclude '.pytest_cache' \
  skill/ ~/.hermes/skills/media/video-report-nemotron/
```

`skill/.env` 或 `~/.hermes/skills/media/video-report-nemotron/.env` 只保留在本机。仓库只提交 `.env.example`。

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
VIDEO_REPORT_OUTPUT_ROOT=
ASR_BACKEND=auto
MLX_ASR_MODEL=mlx-community/nemotron-3.5-asr-streaming-0.6b-8bit
```

你可以替换为任何 OpenAI-compatible API endpoint 和模型名。真实 API key 只放在本地 `.env` 或 shell 环境变量里，不要提交到 Git。

关键配置：

| 变量 | 作用 | 默认值 |
| --- | --- | --- |
| `OPENAI_BASE_URL` | 报告生成和视觉兜底使用的 OpenAI-compatible API base。 | `https://api.openai.com/v1` |
| `OPENAI_TEXT_MODEL` | 最终报告 composer 使用的文本模型。 | `gpt-4.1-mini` |
| `OPENAI_VISION_MODEL` | 只在 OCR 不足时使用的视觉模型。 | `gpt-4.1-mini` |
| `OPENAI_API_KEY` | Provider API key，只能保留在本地。 | 空 |
| `REPORT_LANGUAGE` | 默认最终报告语言：`auto`、`en`、`zh-CN` 等。 | `auto` |
| `VIDEO_REPORT_OUTPUT_ROOT` | 可选输出根目录。为空时，`video_report.py` 写入 `./video-report-output/<source-slug>`。 | 空 |
| `ASR_BACKEND` | 字幕不可用时的 ASR 后端。 | `auto` |
| `MLX_ASR_MODEL` | Apple Silicon MLX Nemotron 模型 repo。 | `mlx-community/nemotron-3.5-asr-streaming-0.6b-8bit` |

配置加载顺序：显式 `--env-file`、当前目录 `.env`、skill 根目录 `.env`、`~/.config/video-report-nemotron/.env`。显式 `--env-file` 会覆盖已有环境变量；自动发现的 `.env` 只填充缺失值。

## 输出模型

这套链路有两层输出：

| 层级 | 生成脚本 | 面向对象 | 说明 |
| --- | --- | --- | --- |
| 转写中间产物 | `video_report.py` | 操作者和下游脚本 | 包含来源元数据、带时间戳转写、启发式摘要和 artifact 路径。 |
| 最终报告 | `video_compose_final_report.py` | 最终用户 | 结构化 Markdown/HTML/PDF，精选图片会插入到相关正文附近。 |

`video_report.py` 生成的 Markdown 不是最终报告。只要用户要完整报告，就继续跑视觉 manifest、抽帧、OCR、必要时视觉兜底，以及最终 composer。

一个典型输出目录如下：

```text
video-report-output/<source-slug>/
├── <title>.json                         # 转写中间 JSON
├── <title>.md                           # 转写中间 Markdown
└── visual/
    ├── visual_manifest.json             # 已审核的抽帧计划
    ├── visual_manifest.frames.json      # 抽帧结果
    ├── visual_manifest.ocr.json         # OCR 结果
    ├── visual_manifest.vision.json      # 可选视觉分析结果
    ├── frames/                          # 精选截图 PNG
    ├── ocr/                             # OCR JSON 文件
    ├── report.md                        # 最终 Markdown
    ├── report.html                      # 最终 HTML
    └── report.pdf                       # 最终 PDF，如有请求
```

## 快速开始

先生成转写和元数据中间产物：

```bash
export VIDEO_REPORT_OUTPUT_ROOT="$PWD/video-report-output"
export REPORT_DIR="$VIDEO_REPORT_OUTPUT_ROOT/QggkUtXNkPo"

.venv-nemotron/bin/python skill/scripts/video_report.py \
  "https://www.youtube.com/watch?v=QggkUtXNkPo" \
  --transcript-source auto \
  --asr-backend auto \
  --language zh-CN \
  --output-dir "$REPORT_DIR" \
  --chunk-seconds 90
```

默认 `--transcript-source auto` 会先尝试平台字幕；字幕不可用时才会提取音频并使用选定的 Nemotron 后端。

然后对生成的 JSON 跑最终报告链路。下面示例假设 JSON 路径是 `$REPORT_DIR/QggkUtXNkPo.json`；如果标题 slug 不同，请使用 `video_report.py` 输出里打印的 JSON 路径。

```bash
.venv-nemotron/bin/python skill/scripts/video_visual_manifest.py \
  "$REPORT_DIR/QggkUtXNkPo.json" \
  -o "$REPORT_DIR/visual/visual_manifest.json"

.venv-nemotron/bin/python skill/scripts/video_capture_frames.py \
  "$REPORT_DIR/visual/visual_manifest.json" \
  -o "$REPORT_DIR/visual/visual_manifest.frames.json" \
  --frames-dir "$REPORT_DIR/visual/frames" \
  --overwrite

.venv-nemotron/bin/python skill/scripts/video_ocr_frames.py \
  "$REPORT_DIR/visual/visual_manifest.frames.json" \
  -o "$REPORT_DIR/visual/visual_manifest.ocr.json" \
  --ocr-dir "$REPORT_DIR/visual/ocr"

.venv-nemotron/bin/python skill/scripts/video_multimodal_frames.py \
  "$REPORT_DIR/visual/visual_manifest.ocr.json" \
  -o "$REPORT_DIR/visual/visual_manifest.vision.json" \
  --env-file skill/.env \
  --analysis-language auto

.venv-nemotron/bin/python skill/scripts/video_compose_final_report.py \
  "$REPORT_DIR/QggkUtXNkPo.json" \
  "$REPORT_DIR/visual/visual_manifest.vision.json" \
  --markdown "$REPORT_DIR/visual/report.md" \
  --html "$REPORT_DIR/visual/report.html" \
  --pdf "$REPORT_DIR/visual/report.pdf" \
  --env-file skill/.env \
  --report-language zh-CN
```

强制本机 ASR：

```bash
.venv-nemotron/bin/python skill/scripts/video_report.py ./video.mp4 \
  --transcript-source asr \
  --asr-backend auto \
  --language en-US \
  --output-dir reports/local-video
```

## YouTube 新访问策略

这一节只针对 YouTube，不是本地文件、Bilibili 或其他 `yt-dlp` 来源的通用策略。

YouTube 现在更频繁地对匿名抓取触发 bot 校验、播放完整性校验或 PO-token/player challenge。典型现象包括：`Sign in to confirm you're not a bot`、反复下载 player JavaScript 时出现 `IncompleteRead`、`n challenge solving failed`，或者格式列表里只有 `sb0`/`sb1` 这类 storyboard 条目。遇到这些情况时，不要把标题、简介、推荐、广告或 live chat 当成视频口播正文来写报告。

推荐顺序：

1. 先尝试字幕/自动字幕；如果视频需要登录态，就使用浏览器 cookies 或导出的 `cookies.txt` 做诊断：

```bash
yt-dlp --cookies-from-browser safari --no-playlist --list-subs --ignore-no-formats URL
yt-dlp --cookies cookies.txt --no-playlist --list-subs --ignore-no-formats URL
```

2. 如果 cookies 可用但视频没有字幕，优先让用户提供本地音视频文件，然后走正常 ASR 链路。
3. 如果 YouTube 只开放元数据、不开放可播放媒体格式，可以少量尝试 `yt-dlp` 的 player/client 诊断参数，然后停止；不要无限循环尝试随机 extractor 设置。
4. 如果最终仍然只有元数据或 storyboard 格式，就要求用户提供复制出来的字幕、本地媒体文件，或可用的 `cookies.txt`。只有在用户明确要求时，才生成 metadata-only 报告。

更详细的 YouTube 诊断参考见 [skill/references/youtube-cookies-po-token.md](../skill/references/youtube-cookies-po-token.md)，强化版 YouTube 工作流见 [skill/references/youtube-video-report-hardening.md](../skill/references/youtube-video-report-hardening.md)。

## 稳定视频抽帧

`video_capture_frames.py` 现在会先把 URL 视频源下载成本地稳定 MP4，再用 `ffmpeg` 按时间点抽帧。这样可以避开直接读取 `googlevideo.com` 签名 URL 时常见的 TLS EOF、URL 过期、seek 失败等问题。默认抽帧完成后会删除临时视频；如果需要保留可审计的视频文件，请加 `--keep-video`。

```bash
.venv-nemotron/bin/python skill/scripts/video_capture_frames.py \
  reports/QggkUtXNkPo/visual/visual_manifest.json \
  -o reports/QggkUtXNkPo/visual/visual_manifest.frames.json \
  --frames-dir reports/QggkUtXNkPo/visual/frames \
  --download-dir reports/QggkUtXNkPo/visual/source_video \
  --overwrite
```

需要保留下载视频时用 `--keep-video`；明确想走旧的直接流式 URL 行为时用 `--no-download`。

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

## 脚本速查

| 脚本 | 必要输入 | 主要输出 | 使用场景 |
| --- | --- | --- | --- |
| `video_report.py` | URL 或本地媒体路径 | 转写 JSON/Markdown | 每次任务的起点。优先字幕，然后 Nemotron ASR。 |
| `video_visual_manifest.py` | `video_report.py` JSON | 视觉 manifest | 判断哪些转写时间块需要视频画面。 |
| `video_capture_frames.py` | 视觉 manifest | 截图 PNG 和 frames manifest | 只对审核后的时间点抽帧；URL 默认先下载临时本地视频。 |
| `video_ocr_frames.py` | frames manifest | OCR manifest 和 OCR JSON | 从精选截图中提取文字。 |
| `video_multimodal_frames.py` | OCR manifest | vision manifest | 用视觉模型分析 OCR 不足的截图。 |
| `video_compose_final_report.py` | 转写 JSON 和视觉 manifest | 最终 Markdown/HTML/PDF | 生成面向用户的最终报告。 |
| `video_build_visual_report.py` | 视觉 manifest | 视觉审计 Markdown/HTML/PDF | 用户明确要证据链时使用。 |
| `video_render_markdown.py` | 已有 Markdown | HTML/PDF | 渲染事实核查补充稿或手写报告，避免丢表格/图片。 |

## 渲染已有 Markdown

当你已经有 Markdown 报告或事实核查补充稿，只需要可靠生成 HTML/PDF 时，用 `video_render_markdown.py`。它复用 skill 的渲染器，能保留表格、图片、粗体、inline code 和相对图片路径。

```bash
.venv-nemotron/bin/python skill/scripts/video_render_markdown.py \
  reports/QggkUtXNkPo/visual/report.md \
  --html reports/QggkUtXNkPo/visual/report.html \
  --pdf reports/QggkUtXNkPo/visual/report.pdf \
  --title "QggkUtXNkPo Report"
```

报告质量检查参考 [skill/references/report-quality-pitfalls.md](../skill/references/report-quality-pitfalls.md)。

## Hermes CLI

安装或更新 skill：

```bash
mkdir -p ~/.hermes/skills/media
rsync -a --delete \
  --exclude '.env' \
  --exclude '__pycache__' \
  --exclude '.pytest_cache' \
  skill/ ~/.hermes/skills/media/video-report-nemotron/
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

测试覆盖字幕优先策略、仅 Nemotron 的 ASR 后端选择、强制输出语言、视觉 manifest、环境变量加载、报告生成和 Markdown-to-HTML/PDF 渲染行为。

## 故障处理

| 现象 | 常见原因 | 处理方式 |
| --- | --- | --- |
| `Missing required command: yt-dlp` | 缺 Python 包或可执行文件。 | 在当前环境安装 `yt-dlp`，或把可执行文件放到 `PATH`。 |
| `No Nemotron ASR backend` | 没有安装受支持的本地 Nemotron 后端。 | Apple Silicon 安装 GitHub 版 `mlx-audio`；其他平台先接入明确的 Nemotron 后端。 |
| YouTube 只有 `sb*` storyboard 格式 | YouTube 播放完整性或 player challenge 阻断媒体格式。 | 使用浏览器/导出 cookies，参考 YouTube 文档诊断，或改用用户提供的媒体/字幕。 |
| `ffmpeg` 读取直链失败 | 签名媒体 URL 过期，或直接流式 seek 不稳定。 | 让 `video_capture_frames.py` 先下载临时本地视频，不要使用 `--no-download`。 |
| OCR 没有有效文字 | 画面不是文字型内容，或 OCR 依赖缺失。 | 安装 LiteParse/ImageMagick，再对弱 OCR 帧运行视觉兜底。 |
| PDF 生成失败 | 缺 Playwright 浏览器。 | 执行 `playwright install chromium`；必要时用 `uvx --from playwright playwright pdf`。 |
| 最终报告没有按指定语言输出 | Provider 没遵循 `--report-language`。 | composer 会对明显语言错误自动重试一次；仍失败时换更强模型或更明确的语言提示。 |

## 报告质量检查

- 使用 `video_report.py` 打印的 JSON 路径；标题生成的文件名可能和视频 ID 不同。
- 对包含幻灯片、仪表盘、产品演示、图表、法律/财经声明的视频，抽帧前先检查 `visual_manifest.json`。
- 没有实际截图和 OCR/vision 产物支撑时，不要声称有视觉证据。
- 截图应插在相关正文附近，不要堆在报告末尾。
- 做事实核查时，用一手或高质量实时来源验证硬事实，并把事实和观点分开。
- 交付前检查最终 Markdown，以及渲染后的 HTML/PDF。

## 安全说明

- 真实 `.env` 文件已在 `.gitignore` 中忽略。
- 下载的视频、音频和中间媒体文件不提交。
- 示例报告只保留小型可复现产物，不包含模型权重和原始媒体下载。
- 公开视频内容仍可能受平台条款和版权限制；请只处理你有权处理的内容。
