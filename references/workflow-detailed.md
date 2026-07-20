# 公共工作流程

本文件只描述三个分支共享的流程。具体删留、结构和验收必须读取唯一对应的 `modes/*.md`。

## Step 0：路由并加载唯一分支

优先采用用户明确指定的主稿。商务、客户、销售、合作、访谈和普通多人对话如未明确主稿，必须先展示三个选项，推荐“交流实录稿”并等待用户确认；不得因标题或内容看似清楚而自动路由。明确复盘意图、课程意图或交流实录意图时可直接路由。确认后只读取对应的 `exchange.md`、`review.md` 或 `learning.md`。

## Step 1：取得事实源与确定落点

| 类型 | 事实源 |
|---|---|
| audio / recorder_audio | `audio.original` |
| link / web_page | `web_page.content` |
| 用户文件或文本 | 用户给定原文 |

记录标题、note_id、note_type、原文长度、起止时间和保存落点。禁止用 AI 总结字段替代事实源。

保存约定：

- 默认交付 Get笔记：新建精炼笔记，不改原笔记，不在本地一级目录留下永久过程文件。
- 本地交付：当前最终稿直接放用户指定位置；若未另行指定且项目根为 `E:\workbuddy\逐字稿整理`，最终稿放该一级目录。每个任务只保留一份当前最终稿。
- 过程文件：统一放 `.workbuddy/temp/transcript-refine/<task-id>/`，不为每篇笔记建立永久文件夹。
- 原始逐字稿：默认不另存；Get笔记原笔记或用户原文件继续作为事实源。只有已返工、人物复杂、来源可能失效或用户明确要求时，才把副本放入本任务临时目录。
- 更新本地既有稿：仅在用户明确要求时执行；先把旧版复制到 `backups/transcript-refine/<timestamp>/`，一级目录只留下当前版，不生成 V2、V3 或上下篇过程稿。

## Step 2：建立源段与人物锁定

长文本按自然话题建立连续 `Sxx` 源段，记录时间/位置、人物、主题和必留信息。

单人、明确双人或 ASR 标签可靠时不创建人物文件。多人且 ASR 标签无法直接对应真人时，在本任务临时目录建立 `speaker-map.json`：

```json
{
  "version": 1,
  "invalidated": false,
  "people_count": 3,
  "asr_channel_count": 2,
  "mappings": [
    {
      "asr_label": "说话人1",
      "output_label": "说话人A",
      "person": null,
      "candidates": ["候选甲", "候选乙"],
      "evidence": ["同一声道包含多个角色，暂不能稳定拆分"],
      "confidence": "low",
      "status": "unresolved"
    }
  ]
}
```

每项固定记录 ASR 标签、输出标签、人物或候选人、证据、置信度和锁定状态。`locked` 才能使用真人姓名；`unresolved` 必须使用低置信标签。人物尚未确认时可以继续整理，但只能产出低置信标签稿，不能伪装成已确认的姓名稿。

用户纠正人物后，立即把旧表标记 `invalidated: true`，旧表和受影响稿件不得继续通过；重新核对全部源段、人物标签、关键问答、数字、承诺与行动项后再建立新表。

## Step 3：公共精炼

每段依次做机械清理、ASR 修正、语义修正、分支删留和覆盖登记。公共方法见 `refine-method.md`，逐句检查见 `sentence-check.md`。

## Step 4：分支组装与检查

只使用所选分支规定的结构。抽查开头、中段、结尾，核对人物、数字、产品名、案例、限定条件和全部 `Sxx` 去向。

普通命令：

```powershell
python scripts/validate_transcript_artifact.py --mode learning --draft output.md --source original.txt --json
```

复杂多人交流稿：

```powershell
python scripts/validate_transcript_artifact.py --mode exchange --draft output.md --source original.txt `
  --speaker-map .workbuddy/temp/transcript-refine/<task-id>/speaker-map.json `
  --people-count 3 --asr-channel-count 2 --json
```

确定性脚本通过不代表语义合格。

## Step 5：保存与 read-back

- 新建 Get笔记用 `refine-transcript.py`；更新用户明确指定的既有笔记用 `update-note.py`。
- 主稿类型必须在调用保存脚本前由 Skill 决定，脚本不得重新路由。
- 成功必须满足标题、正文长度和规范化 SHA256 read-back 一致。换行统一为 LF，只忽略正文末尾一个换行；其余差异均失败。
- 保存脚本永不删除或移动输入文件。旧 `--no-cleanup` 仅兼容接收，不产生行为。
- 清理过程文件必须由用户确认后独立执行；保存流程不附带清理。
