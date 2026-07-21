# 安装与使用

## 前置条件

- Git
- Python（用于校验和可选 Get笔记适配器）
- Codex 或 Claude Code（二选一即可）

## Codex

```powershell
git clone https://github.com/TANG9410/transcript-refine-v3.git
cd transcript-refine-v3
.\install.ps1 -Target Codex
```

默认安装到当前 Windows 用户的 `.codex\skills\transcript-refine-v3`。已存在同名 skill 时脚本会停止，不会覆盖；确实需要覆盖时才显式使用 `-Force`。

## Claude Code

```powershell
.\install.ps1 -Target Claude
```

默认安装到当前 Windows 用户的 `.claude\skills\transcript-refine-v3`。

## 手动安装

将仓库内容复制到对应的 skills 根目录，目录名保持 `transcript-refine-v3`；不要复制 `.git`、个人配置或运行输出。

## 可选 Get笔记适配器

本地 Markdown 是默认交付。若明确需要读写 Get笔记：

1. 使用者自行安装 Python 依赖：`pip install -r requirements-getnote.txt`。
2. 将自己的配置保存在本机，不要提交到仓库。
3. 运行保存脚本时传入 `--config <本机配置路径>`，或使用既有本机配置路径。

缺少配置时，脚本会安全失败并提示改用本地 Markdown；它不会尝试读取、打印或创建凭证。