---
name: transcript-refine-v3
version: 3.8.0
description: |
  逐字稿精炼 Skill：把 Get笔记录音、网页转写或用户原文整理为交流实录稿、复盘纪要稿或学习整理稿。
  商务、客户、销售、合作、访谈及多人对话未明确交付形态时，先推荐交流实录稿并等待确认；明确要求交流实录、复盘纪要或学习整理时按意图执行。
metadata:
  requires:
    skills: ["getnote"]
    bins: ["python"]
  philosophy: "公共路由，单分支强制加载，人物先锁定，保存可验证"
---

# 逐字稿精炼 Skill

## 公共硬规则

1. 事实源只取 `audio.original`、`web_page.content` 或用户原文，不得用 AI 总结替代。
2. 主稿只有 `交流实录稿`、`复盘纪要稿`、`学习整理稿`；内容素材只是二次加工。
3. 商务、客户、销售、合作、访谈和普通多人对话，用户未明确交付形态时必须展示三选一，推荐“交流实录稿”并等待确认；明确说交流实录、保留对话过程时用交流实录，明确要求复盘、纪要、总结、核心内容或行动项时用复盘纪要，课程、讲座、读书分享和知识型长独白用学习整理。
4. 多人任务必须先校准人物再写最终稿。人数多于 ASR 声道数且无法直接对应真人时，必须建立临时 `speaker-map.json`；未锁定人物只能使用“说话人A”等低置信标签，禁止猜姓名。
5. 未指定保存位置时默认新建 Get笔记，原笔记不变；明确本地交付时只保留一份当前最终 Markdown。更新既有稿必须由用户明确指定并先备份旧版。
6. 分段稿、覆盖表、临时原文、人物表和校验产物只放 `.workbuddy/temp/transcript-refine/<task-id>/`；默认不额外复制原始逐字稿。
7. 保存脚本不得删除或移动本地文件；保存成功必须以 read-back 的标题、正文长度和规范化 SHA256 一致为准。
8. 长文本建立连续 `S01`、`S02`……源段；保留率只作异常提示，不能单独判定合格。用户纠正人物后，旧映射和受影响稿件立即失效，必须全局复查。

## 强制分支加载

路由完成后，写正文前必须完整读取且只读取一个主稿分支合同：

| 主稿 | 必须读取 |
|---|---|
| 交流实录稿 | `references/modes/exchange.md` |
| 复盘纪要稿 | `references/modes/review.md` |
| 学习整理稿 | `references/modes/learning.md` |

未读取对应分支不得写稿；`delivery-modes.md` 只是兼容索引，不能替代分支合同。

## 公共执行骨架

1. 先完成交付形态门控；未确认的商务或多人对话不得进入事实源处理或写稿。
2. 读取事实源并确定唯一主稿分支；完整读取对应分支合同，建立 `Sxx` 源段和必要的人物映射。
3. 按自然话题精炼、组装正文并抽检开头、中段、结尾。
4. 运行 `scripts/validate_transcript_artifact.py --mode <exchange|review|learning>`；复杂交流稿同时传入 `--speaker-map`。
5. 按约定保存并 read-back；清理只能作为用户确认后的独立操作。

## 公共资源

- 公共流程、人物锁定和文件管理：`references/workflow-detailed.md`
- 三分支兼容索引：`references/delivery-modes.md`
- 精炼方法：`references/refine-method.md`
- 逐句检查：`references/sentence-check.md`
- 销售证据与多源合并：`references/sales-review-transcripts.md`
- ASR 映射：`data/error-mapping.json`
- 工具说明：`references/scripts-guide.md`
- 回归证据：`evals/routing-cases.json`、`evals/mode-quality-cases.json`
- 输出风险：`reports/output-risk-profile.md`

## 安装

规范源位于 `.workbuddy/skills/transcript-refine-v3`；稳定后完整镜像到 Codex 与 Claude Code 同名目录。
