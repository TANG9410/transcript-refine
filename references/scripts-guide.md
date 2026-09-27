# 底层脚本兼容与排障

正常任务使用 `transcript_task.py prepare / review / deliver`，默认交付本地文件。下列命令继续兼容旧任务和异常排查，不要求模型逐个调度。Get 原文优先使用 MCP 已保存的真实返回；无可读返回才使用固定只读接口。用户明确要求 Get 交付时，新入口通过现有保存实现处理，不以 MCP 故障为使用条件。

## 前置条件

- 已按 `SKILL.md` 确定事实源、唯一主稿模式和最终稿。
- 已运行带 `--source` 的最终检查，验证报告显示 `final_ready=true`，且报告中的稿件 SHA256 与待保存文件一致。
- 原始事实源超过 `35000` 字符时，拆篇门控已经处理；未获确认不得用 CLI 自动拆篇或保存多篇。
- 使用机器上已有的 Getnote 登录配置，禁止打印密钥或在命令中内联凭证。

最终检查必须使用 `--report-out <最终校验报告.json>` 直接保存校验器生成的完整结果；手写或裁剪报告无效。保存门禁还会核对完整复核证据快照、复核报告哈希和证据摘要；只有报告显示 `final_ready=true`、源对齐复核完成且没有未解决检查项，才把该文件交给保存脚本。

## 原文与复核哈希

先按 `references/input-preparation.md` 使用固定 `prepare_transcript_inputs.py`：`source` 从真实返回原样提取；无可读返回文件才使用固定只读接口。不得临时拼 HTTP 地址或重抄原文。

结构校验通过 `--report-out` 保存完整 JSON；哈希在顶层，不需要 final-check。按当前模式合同完成原文对照和 v1 复核报告后，运行 `review-hashes` 重算核对并回填哈希；文件已变则重新复核和生成结构报告。

风险词表只提示，不决定删留或风险是否存在；每条引文都须真实，包含上下文。词表漏检的 checked 或误报的 not_present，提供具体 `semantic_reason` 和所需真实引文，不用无关公共子串凑证据。未核对、可能改变正文的检查项必须阻断；不能为了通过检查清空它们。

## 新建普通 Getnote

```powershell
python scripts/refine-transcript.py --note-id <源ID> --file <最终稿.md> `
  --format "<交流实录稿|复盘纪要稿|学习整理稿>" `
  --validation-report <最终校验报告.json> --json
```

脚本只保存已经完成路由和校验的稿件，不自行选择模式。两个保存脚本内部走 OpenAPI，依赖 Python `requests`；运行前先确认所调用的解释器已安装该包。

## 更新用户指定的既有精炼稿

```powershell
python scripts/update-note.py --note-id <目标ID> --file <最终稿.md> `
  --validation-report <最终校验报告.json> --json
```

只有用户明确要求更新并指定目标精炼笔记时才更新；明确要求 Get 新建才新建。未指定交付平台时保存本地，原笔记保持不变。

## 成功判定与安全边界

- JSON 结果必须显示保存成功，且 read-back 的 note ID、标题、正文长度和规范化 SHA256 一致；不一致即失败。
- 缺少 `final_ready=true` 的校验报告、报告哈希不匹配或报告已经失效时，脚本必须拒绝保存。
- 两个保存脚本都不得删除或移动输入文件；旧 `--no-cleanup` 仅兼容参数，不产生清理。
- 不提供自动清理；发布、删除或清理仍需独立确认。
