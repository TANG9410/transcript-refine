# 安装与使用

## 前置条件

- 支持 skill 的 AI 助手，例如 Codex 或 Claude Code。
- Python 3.10+；`python --version` 可查看版本。部分系统使用 `python3`。
- Git（或从 GitHub 下载并解压 ZIP）。

本地文件流程无第三方 Python 依赖。脚本负责机械处理，仍需 AI 助手阅读 skill、整理正文并完成语义复核。

## Windows

```powershell
git clone https://github.com/TANG9410/transcript-refine.git
cd transcript-refine
.\install.ps1 -Target Codex -WhatIf
.\install.ps1 -Target Codex
```

默认安装到 `~/.codex/skills/transcript-refine`。Claude Code 使用 `-Target Claude`，安装到 `~/.claude/skills/transcript-refine`。

安装脚本只复制本地仓库内明确列出的发布文件，不下载代码，不复制 `.git`、运行记录或本地配置。已有同名目标时拒绝覆盖。只有检查并备份后才使用 `-Force`；它叠加文件，不清理目标目录中的旧文件。

可用 `-DestinationRoot <skills根目录>` 指定安装位置。安装完成后重新打开助手会话。

## macOS / Linux

克隆或解压后，在仓库目录运行以下命令（Codex）：

```sh
target="$HOME/.codex/skills/transcript-refine"
if [ -e "$target" ]; then
  echo "目标已存在，请先检查并备份：$target"
else
  mkdir -p "$target" &&
  tar --exclude='__pycache__' --exclude='.test-tmp-root' \
      --exclude='outputs' --exclude='.transcript-refine' --exclude='.workbuddy' \
      --exclude='config.json' --exclude='getnote-config.json' --exclude='.env' \
      -cf - SKILL.md manifest.json LICENSE README.md INSTALL.md CONTRIBUTING.md SECURITY.md requirements-getnote.txt agents references scripts evals | \
    tar -xf - -C "$target"
fi
```

Claude Code 将第一行的 `.codex` 改为 `.claude`。其他助手请按其 skill 安装约定放置同名目录。macOS / Linux 命令尚未在对应系统实测。

## 已安装旧名称时

旧版目录名是 `transcript-refine-v3`。新安装使用 `transcript-refine`，不会覆盖旧目录。

若两者同时安装，先用 `$transcript-refine` 明确指定新版。确认新版符合需要后，可自行把旧目录备份并移出助手扫描的 skills 目录，避免两个版本同时被选中；安装器不会自动删除旧版。

历史任务目录与来源文件保留原样。旧任务不自动迁移；内部 v1 校验报告标识保持兼容，不受产品改名影响。

## 第一次使用

把原始 `.txt` 或 `.md` 文件提供给 AI 助手，并说明目标形式，例如：

> 使用 $transcript-refine，把这份逐字稿整理为交流实录稿，轻度精炼，结果保存到本地。

助手按 skill 运行三个阶段：

1. `prepare`：原文原样落盘，创建待填复核输入。
2. `review`：展示原文和成稿对照，模型阅读后填写判断和疑点。
3. `deliver`：检查证据与版本一致，再保存成稿并回读。

从 skill 目录查看命令用法：

```powershell
python scripts/transcript_task.py --help
python scripts/transcript_task.py prepare --help
```

如需自己准备任务目录，可使用：

```powershell
python scripts/transcript_task.py prepare --task-dir <空任务目录> --mode exchange --source-file <原文.txt>
```

不要手工批量把复核状态改成通过。用户提供且确认的术语表按当前任务加载；包内不附带维护者的私人术语表。

## 可选 Get笔记接入

只在你明确选择 Get笔记来源或交付时启用。可使用自己已配置的 Getnote MCP 获取真实返回；本地文件整理无需安装 MCP。

直接使用 OpenAPI 时，准备自己的本地配置文件，包含 `api_key` 和 `client_id` 两个字符串字段。配置放在仓库外，勿提交或在对话中公开。准备阶段传 `--config <配置路径>`；任务仅记录路径，保存阶段复用该配置。未显式指定时会尝试已有 `~/.workbuddy/skills/getnote/config.json` 或 `~/.getnote/config.json`。

```powershell
python scripts/transcript_task.py prepare --task-dir <空任务目录> --mode exchange --note-id <源笔记ID> --config <本机配置路径>
```

读取原文的工具使用 Python 标准库。需要**写入 Get笔记**时，先安装可选依赖：

```powershell
python -m pip install -r requirements-getnote.txt
```

复核完成后，明确指定交付方式：

```powershell
# 新建精炼笔记，保留源笔记
python scripts/transcript_task.py deliver --task <任务目录> --destination get-new --output-title <已校正标题>
# 更新你明确指定的既有精炼笔记
python scripts/transcript_task.py deliver --task <任务目录> --destination get-update --target-note-id <精炼笔记ID>
```

未指定 `--destination` 时仍默认本地交付，需要 `--output <最终稿.md>`。工具拒绝将本任务源笔记作为更新目标。

缺少配置或写入依赖会报错，不自动创建凭证。底层保存脚本也支持 `--config`；兼容用法见 [脚本说明](references/scripts-guide.md)。
