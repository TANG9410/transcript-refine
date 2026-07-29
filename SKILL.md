---
name: transcript-refine-v3
version: 3.9.1
description: |
  逐字稿精炼 Skill：把本地逐字稿、网页转写或用户原文整理为交流实录稿、复盘纪要稿或学习整理稿；Get笔记读写是可选适配器。
  商务、客户、销售、合作、访谈及多人对话未明确交付形态时，先推荐交流实录稿并等待确认；明确要求交流实录、复盘纪要或学习整理时按意图执行。
  交流实录稿默认轻度精炼，不再二次确认；只有用户明确要求时才切换为中度或重度整理。
metadata:
  requires:
    bins: ["python"]
  philosophy: "公共路由，交流轻度保真，单分支强制加载，人物按需锁定，保存可验证"
---

# 逐字稿精炼 Skill

## 公共硬规则

1. 事实源只取 `audio.original`、`web_page.content` 或用户原文，不得用 AI 总结替代。
2. 主稿只有 `交流实录稿`、`复盘纪要稿`、`学习整理稿`；内容素材只是二次加工。
3. 商务、客户、销售、合作、访谈和普通多人对话，用户未明确交付形态时必须展示三选一，推荐“交流实录稿”并等待确认；明确说交流实录、保留对话过程时用交流实录，明确要求复盘、纪要、总结、核心内容或行动项时用复盘纪要，课程、讲座、读书分享和知识型长独白用学习整理。
4. 交流实录稿的“主稿类型”和“精炼尺度”是两个维度：进入交流分支后默认轻度精炼，不再追加询问；用户明确说“中度”或“重度”时才切换。三档只改变清理力度，任何档位都不得摘要化或重组对话。
5. 多人任务必须先校准人物再写最终稿。人数多于 ASR 声道数且无法直接对应真人时，必须建立临时 `speaker-map.json`；未锁定人物只能使用“说话人A”等低置信标签，禁止猜姓名。
6. 未指定保存位置时默认输出一份当前最终 Markdown；只有用户明确要求且本机已配置 Get笔记时，才新建或更新 Get笔记。更新既有稿必须由用户明确指定并先备份旧版。
7. 分段稿、覆盖表、临时原文、人物表和校验产物只放 `.workbuddy/temp/transcript-refine/<task-id>/`；默认不额外复制原始逐字稿。
8. 保存脚本不得删除或移动本地文件；保存成功必须以 read-back 的标题、正文长度和规范化 SHA256 一致为准。
9. 长文本或返工审计可建立连续 `S01`、`S02`……源段；普通交流稿不强制维护完整覆盖表。保留率只作异常提示，不能单独判定合格。用户纠正人物后，旧映射和受影响稿件立即失效，必须全局复查。
10. 当前 `SKILL.md` 与已加载的唯一分支合同是执行时的最高规则；旧版记忆、历史任务总结和回收站版本只能用于维护追溯，不得扩大为六种交付形式，也不得覆盖当前路由、尺度和删留规则。

## 强制分支加载

路由完成后，写正文前必须完整读取且只读取一个主稿分支合同：

| 主稿 | 必须读取 |
|---|---|
| 交流实录稿 | `references/modes/exchange.md` |
| 复盘纪要稿 | `references/modes/review.md` |
| 学习整理稿 | `references/modes/learning.md` |

未读取对应分支不得写稿；`delivery-modes.md` 只是兼容索引，不能替代分支合同。
交流实录稿的删留、顺序和通顺修正规则以 `references/modes/exchange.md` 为准；共享方法与其冲突时必须服从交流分支。

## 公共执行骨架

1. 先完成交付形态门控；未确认的商务或多人对话不得进入事实源处理或写稿。
2. 读取事实源并确定唯一主稿分支；完整读取对应分支合同，按任务复杂度建立必要的 `Sxx` 源段和人物映射。
3. 按唯一分支合同精炼和组装正文。交流实录稿必须沿原始时间顺序和话轮推进，只能切分连续时间段，不能按主题跨段重组；抽检开头、中段、结尾。
4. 运行 `scripts/validate_transcript_artifact.py --mode <exchange|review|learning>`；复杂交流稿同时传入 `--speaker-map`。
5. 默认交付最终 Markdown；选择 Get笔记适配器时再保存并 read-back。清理只能作为用户确认后的独立操作。

## 公共资源

- 公共流程、人物锁定和文件管理：`references/workflow-detailed.md`
- 三分支兼容索引：`references/delivery-modes.md`
- 精炼方法：`references/refine-method.md`
- 逐句检查：`references/sentence-check.md`
- 销售证据与多源合并：`references/sales-review-transcripts.md`
- ASR 映射：`data/error-mapping.json`
- 工具说明与可选 Get笔记适配器：`references/scripts-guide.md`
- 回归证据：`evals/routing-cases.json`、`evals/mode-quality-cases.json`
- 输出风险：`reports/output-risk-profile.md`

## 安装

使用说明见 `INSTALL.md`。可用 `install.ps1 -Target Codex` 或 `install.ps1 -Target Claude` 安装已 clone 的本地副本；安装脚本不会下载或执行远端代码，且默认拒绝覆盖已有 skill。
