---
name: transcript-refine
description: |
  整理本地逐字稿、网页转写或用户原文，支持交流实录、复盘纪要、学习整理三种主稿；Get笔记为可选接入。
  主稿明确时直接执行；商务或多人对话未明确时，展示三项并等待选择。
metadata:
  version: "4.1.2"
  requires:
    bins: ["python"]
  philosophy: "准备材料、集中整理复核、统一检查交付"
---

# 逐字稿精炼 Skill

## 选择主稿

| 主稿 | 唯一必读模式 | mode |
|---|---|---|
| 交流实录稿 | `references/modes/exchange.md` | exchange |
| 复盘纪要稿 | `references/modes/review.md` | review |
| 学习整理稿 | `references/modes/learning.md` | learning |

“保留对话过程/交流实录”选交流；“复盘/纪要/总结/行动项”选复盘；课程、讲座、读书或知识型长独白选学习。商务或多人对话未明确主稿时展示三项并等待，可推荐交流。交流默认轻度精炼，仅用户明确要求才变更尺度。

默认读本文件和所选模式；用户提供并确认了术语表时再读取，仅用于当前材料的术语校准。使用 `scripts/transcript_task.py`，参数查 `--help`，无需读源码。排障或兼容旧命令才查 `references/input-preparation.md`、`references/scripts-guide.md`。

交流实录稿必须在正文前保留三项可追溯表头：`会议时间`、`参会人员`、`原始笔记`。只使用原始笔记或录音元数据；缺失时写“未提供”或“待补”，不得猜测或拼造链接。新任务交付会检查这三项；旧任务和旧成稿继续兼容。

## 一、准备材料：prepare

任务目录 `.transcript-refine/<task-id>/` 须为空或不存在，真实返回文件放目录之外：

```powershell
# 可选 Get 接入：宿主已保存真实返回
python scripts/transcript_task.py prepare --task-dir <目录> --mode <mode> --note-id <ID> --response-file <真实返回.json>
# MCP 只有内联返回：从 WorkBuddy 会话 JSONL 提取该次调用，不重抄；其他宿主优先使用真实返回 JSON
python scripts/transcript_task.py prepare --task-dir <目录> --mode <mode> --note-id <ID> --response-file <会话.jsonl> --call-id <调用编号>
# 用户原文或真实缓存
python scripts/transcript_task.py prepare --task-dir <目录> --mode <mode> --source-file <原文.txt>
```

仅在用户选择 Get笔记来源时使用可选接入，凭证由使用者自行配置（见 `INSTALL.md`）。Get 优先 MCP：录音用 `get_note_transcript`，其他原文用 `get_note_original`，`get_note` 兜底。无可读返回时省略 `--response-file/--call-id`，由 prepare 固定只读接口取数；禁止模型重抄或临时拼接口。工具检查输入后落盘，生成 `task.json`、模型填写的 `review-input.json` 和程序维护的 v1 `review-report.json`。

`audio` / `recorder_audio` / `meeting` 取 `audio.original`，网页取 `web_page.content`，用户文件以原文为准；摘要和标题不能替代事实源。异常时间保留并报告，不按时长判断接口故障。

原文超过 `35000` 字符，写稿前只提出一次拆篇方案（篇数、标题、连续边界）并等待选择；确认前不自动拆篇或交付。`10000` 字和录音时长不触发固定切块。两个以上事实源或用户明确要求合并才启用多源：区分主时间线、补充与校验来源，冲突待确认；单源完全跳过。

## 二、模型整理与集中复核：review

读完原文，先校准实体、术语、数字和异常表达，再按唯一模式写 `draft.md`。证据优先用户确认/认可术语表，其次独立事实源，再次稳定重复和上下文；同一 ASR 错词、标题和摘要不能相互自证。高置信才修正，证据不足保留原词并在对应位置注明疑点。校正成稿标题与文件名，原始笔记标题不动。

结合连续上下文理解人物和问答。归属不稳定时建立并持续修正 `speaker-map.json`，记录原标签、原句及段内位置；不编细分时间、不猜姓名、不把局部线索扩散到整段。设备标签不足时沿用有证据支持的低置信标注；声学分离仅在用户单独提出时处理。交流保留时间和话轮顺序，学习与复盘按各自模式组织。

短稿直接写；分批时传 `--parts <有序文件列表>`，保留分稿，块间加两个换行。删回应、合并人物或改数字须结合上下文判断，不按字词、长度或原标签自动决定。

只在 `review-input.json` 选择双方复核行范围，例如 `{"kind":"line","start":12,"end":18}`，再运行：

```powershell
python scripts/transcript_task.py review --task <任务目录>
```

命令直接显示精简对照并保存 `review.md`；有后续页则用 `review --task <目录> --page N` 只读查看，完整差异用 `--full-diff`。集中核对开头、中段、结尾、数字条件、异议、承诺、人物疑点、插曲及被删短回应；确认句连同所确认内容检查。人物说明、收费概括和下一步附录也与原文、正文核对，不提高确定性或更换行动人。

读完本轮对照后，在 `review-input.json` 填返回的 `review_revision`、逐项判断、保留项及问题。`unresolved_items` 留待核对或影响正文的问题；已如实呈现的源疑点写 `preserved_items` 并说明处理。移除已记录问题须在同项 `resolved_issues` 填 `{"issue":"原问题","handling":"具体处置"}`。工具不重写模型输入；模型不编辑程序报告、不手抄引文或哈希。

词表仅提示；漏检或误报用具体 `semantic_reason` 和真实证据解释，不迁就词表改结论。原文、正文、映射或位置变化后重新 review，旧编号与结论失效、问题保留；输入未变化时重跑不撤销结论。

## 三、统一检查与交付：deliver

```powershell
python scripts/transcript_task.py deliver --task <任务目录> --output <本地最终稿.md>
```

默认本地。用户明确要求 Get 时才指定 `--destination get-new/get-update`；更新须明确目标精炼笔记。工具接受对应本轮的模型判断，生成报告、回填哈希、校验、保存并 read-back。缺输入、旧编号、篡改报告或无说明清除问题均拒绝。失败回到实际问题，不删报告重建、不批量填通过。

`final_ready=true` 表示结构与证据检查通过、模型已提交复核结论，不证明程序懂得或核实了全部语义。保留率仅统计，不作质量门槛或删补字目标。工具不得删除或移动输入；覆盖既有成稿仅限用户明确要求，清理另行确认。

交付说明列事实源、落点、检查及疑点。纠正人物、术语或数字后复查正文、标题、附录和复核。故障归因须核对实际请求、完整返回与本地处理；未定位原因标待验证，不升级成长期指令。旧记忆和案例不得覆盖当前入口与模式。
