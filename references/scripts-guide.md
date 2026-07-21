# 脚本使用说明

默认交付本地 Markdown；校验脚本负责可机械验证的质量信号。Get笔记保存脚本是可选适配器，不是安装或使用本 skill 的前提。

| 脚本 | 单一职责 |
|---|---|
| `refine-transcript.py` | 可选：创建新的 Get笔记并 read-back |
| `update-note.py` | 可选：更新用户明确指定的既有 Get笔记并 read-back |
| `validate_transcript_artifact.py` | 检查交流、复盘、学习三种主稿及可选人物表 |
| `validate_exchange_transcript.py` | 旧交流实录校验命令的兼容包装器 |
| `run_regression.py` | 路由、分支、人物锁定和保存完整性回归 |

## 主稿校验

```powershell
python scripts/validate_transcript_artifact.py --mode learning --draft learning.md --source original.txt --json
```

复杂多人交流稿：

```powershell
python scripts/validate_transcript_artifact.py --mode exchange --draft exchange.md --source original.txt `
  --speaker-map speaker-map.json --people-count 3 --asr-channel-count 2 --json
```

结构错误返回 1；警告默认返回 0，增加 `--strict` 后警告返回 2。旧 `validate_exchange_transcript.py` 命令继续可用。

## 可选：创建新 Get笔记

```powershell
python scripts/refine-transcript.py --note-id <源ID> --file output.md `
  --format "学习整理稿" --json
```

`--format` 必须由 Skill 提前确定，脚本不根据场景重新路由。`--config` 可指向使用者自己的本机 Get笔记配置；配置文件绝不应提交到仓库。

## 可选：更新指定 Get笔记

```powershell
python scripts/update-note.py --note-id <目标ID> --file output.md --json
```

只有用户明确指定更新已有笔记时才使用。

## 保存与安全边界

- 未配置 Get笔记时，继续以本地 Markdown 作为最终交付；只有用户明确选择 Get笔记保存时才运行保存脚本。
- 两个保存脚本都会重新读取已保存笔记，核对标题、规范化正文长度和 SHA256；不一致返回失败。
- 规范化只统一 CRLF/CR 为 LF，并忽略正文末尾一个换行。
- 两个脚本永不删除或移动输入文件。旧 `--no-cleanup` 仍接受，但只是兼容提示。
- 不提供 `--cleanup`；清理必须由用户确认后独立执行。
- `--json` 返回操作类型、note ID、标题核对、正文长度、SHA256 和 read-back 状态。
- 凭证只从既有 Get笔记配置读取；禁止临时内联脚本调用 API。
