# 原文落盘与复核报告（按需查阅）

## 正常执行入口

正常任务只使用 `scripts/transcript_task.py prepare / review / deliver`。prepare 创建 `task.json`、模型输入 `review-input.json` 及程序生成的 v1 `review-report.json`。review 显示真实证据，deliver 接受模型判断、回填哈希、校验并保存。旧任务没有 review_input 字段时沿用旧报告接口，不自动迁移；底层命令仅兼容和排障。

### 报告填写顺序

1. 写好 `draft.md`，只编辑 `review-input.json`。samples 的 opening / middle / ending 各选一组 `source_locator`、`draft_locator`；格式 `{"kind":"line","start":12,"end":18}`，行号从 1 起且含首尾。risk_checks 五类各填 `source_locators`、`draft_locators` 范围列表；无该类风险可暂留空，不由脚本代判。
2. 运行 review，直接读取返回的精简对照及页数；`review --task <目录> --page N` 只读续页，`--full-diff` 只读完整差异。工具自动追加人物说明、收费概括和下一步等附录，须对照原文和正文一起判断。所有选定引文仍完整存在程序报告中。看原始行号可用 `--show-lines source|draft --start N --end N`。
3. 阅读后在模型输入中填本轮 `review_revision`，samples.status 用 `pass` / `fail`，risk_checks.status 用 `checked` / `not_present`；逐项填写 `preserved_items`、疑点及理由，总 `result` 仅核对完毕后填 `pass`。初始 pending 不可交付。工具不改模型输入，不提供一键通过；不用临时脚本批量改状态。
4. 词表漏检但确有风险：checked 加具体 `semantic_reason` 和两侧范围；词表误报：not_present 加具体理由和源文范围。理由不能代替真实引文，也不能用“已核对”敷衍。
5. 修改正文、人物映射或所选位置后重新 review，旧编号失效、问题保留；材料不变时重复 review 不撤销结论。模型只在输入中提交位置和判断，机器引文与哈希禁止手填；deliver 统一维护正式报告。旧自由文字定位仅底层兼容，未验证范围。

`unresolved_items` 指尚未核对、可能改变正文的检查项；`blocking_issues` 指明确阻止交付的问题，非空均阻断。已核对且在正文如实保留的源疑点，写入该项 `preserved_items`，说明原句位置、呈现方式和仍不确定什么，必要时补 `semantic_reason`。对已记录问题，移除前须在同项 `resolved_issues` 填 `{"issue":"完整原问题","handling":"实际处置及依据"}`；工具保留处置历史并写入正式报告，空说明或无对应问题均拒绝。顶层问题就在顶层处置。复核编号仅绑定当前材料，不证明模型理解正确。

### 人物映射（交流归属不稳定时）

通过 review 的 `--speaker-map <路径>` 指定，完整示例可读 `evals/sample-speaker-map-v2-mixed.json`（内容只是格式示例，不可当当前人物证据）。顶层 `version: 2`、`invalidated: false`、`people_count`（当前映射使用的人物/暂定角色数，至少 2）、`asr_channel_count`（原标签数，至少 1）、`mappings`（数组）。每项格式如下：

```json
{"asr_label":"说话人1","output_label":"说话人A","person":null,
 "candidates":["提问方","回应方"],"confidence":"low","status":"unresolved",
 "source_span":{"kind":"time","start":"00:10","end":"00:10"},
 "evidence":["填写原句、段内位置和当前判断依据；不要照抄示例说明"]}
```

不确定项 `person` 留空、status 为 unresolved，confidence 为 low / unknown，candidates 非空。output_label 可用“说话人A”“说话人1”等，或在角色/原标签后明确加“（归属待核）”“（低置信）”“（低置信，原标签示例甲）”；未经确认的实名不能裸写成确定身份。已确认项 status 为 locked，person 等于 output_label，confidence 为 high / medium。

使用映射时须覆盖正文全部输出标签；稳定标签可一条说明，归属变化再分段。一个原标签多次映射，每项均记 source_span；允许同时间戳零长度范围，段内区别写 evidence，不编时间也不派生唯一 ASR 标签。只有不相交的实际片段才能用不同跨度；标签数和暂定角色数不证明真实人数。复盘、学习稿不要求为主题重组建立逐话轮映射。

工具 `scripts/prepare_transcript_inputs.py` 仅用 Python 标准库，复用 `note_io_common.text_sha256`。它处理机械读写，不判断语义、不改变复核结论、不写入 Get。

## source：真实返回直接落盘

优先用工具已保存的完整 JSON 返回文件，不能由模型另写 JSON 冒充返回：

```powershell
python scripts/prepare_transcript_inputs.py source --note-id "<ID>" --response-file "<真实返回.json>" --out-dir "<任务目录/source>"
```

若 WorkBuddy 返回只保存在会话 JSONL 中，加 `--call-id "<对应Getnote调用ID>"`，程序核对请求与返回 ID，只提取这次读工具的实际返回，不复制整段会话。支持标准 MCP JSON 文本封装和 OpenAPI `data.note`。

无可直接读取的返回文件时，省略 `--response-file`，由工具固定调用只读 `GET https://openapi.biji.com/open/api/v1/resource/note/detail?id=<ID>`。凭证从现有 `~/.workbuddy/skills/getnote/config.json` 或 `~/.getnote/config.json` 读取（`api_key` / `client_id`），也可传 `--config <现有配置路径>`；不内联或记录密钥。默认沿用环境代理；`--no-proxy` 仅供已有证据支持的本次连接选择，不能写成通用故障结论。

输出 `response.json`（真实原始返回）、`source.txt`（原文字段原样 UTF-8）、`source-record.json`（来源、字符数、哈希、异常位置及比对结果）。三个文件全部成功、退出码 0 才可使用；非空目标拒绝覆盖。需核对现有 source.txt 时用相同输入加 `--verify-only`，时间改动或新增结尾均失败。

音频只接受 `audio.original` 或专读工具的 `transcript`；网页取 `web_page.content`；纯文本取 `content`。部分 `get_note_original` 版本会回退到摘要，故音频/网页仅有未定位的 `original` 时拒绝并要求完整详情。缺原文、ID 不符、错误响应均不生成事实源。时间倒退只报告行号，不修复、不判接口故障。退出码非 0 时保留证据，不把 `.pending-*` 文件当输出。

## review-hashes：顶层读取、重算后回填

先由校验器保存完整结构报告，不需要 `--final-check`：

```powershell
python scripts/validate_transcript_artifact.py --mode exchange --source "<source.txt>" --draft "<draft.md>" --speaker-state stable --report-out "<structure.json>" --json
python scripts/prepare_transcript_inputs.py review-hashes --validation-report "<structure.json>" --source "<source.txt>" --draft "<draft.md>" --review-report "<review-report.json>"
```

若结构检查使用人物映射，两条命令都追加 `--speaker-map "<speaker-map.json>"`，并按实际选择 `--speaker-state unstable`。复核报告沿用 v1，由人先完成原文对照和结论填写；哈希可先留空。第二条命令默认仅更新该报告三个哈希字段，可用 `--output <新报告路径>` 保留原报告。

哈希在校验 JSON **顶层**，不从 `stats` 取。工具重算当前文件确认一致后才回填；缺字段、裁剪报告、文件已变或缺映射输入均拒绝写入。文本哈希统一换行、去 BOM、只忽略末尾一个换行；它与来源记录中的原始字节哈希用途不同。修改正文或映射后需重新语义复核，再重跑结构报告、回填和最终校验。工具不把 pending/fail 改成 pass，也不清空未解决项。
