# 贡献指南

欢迎改进整理规则、校验、文档与安装体验。

- 只使用自行构造或明确获准公开的最小样例；不要提交私人原文、录音、客户信息、笔记 ID 或凭证。
- 保持默认本地 Markdown 交付。外部服务接入由使用者明确选择。
- 修改行为时，补充能验证该行为的离线测试；不要用固定措辞测试替代整理质量判断。

在仓库目录运行：

```powershell
python -X utf8 -m unittest discover -s scripts -p "test_*.py"
python -X utf8 scripts/run_regression.py
python -X utf8 scripts/test_save_scripts.py
python -X utf8 scripts/test_optional_getnote.py
```

上述测试使用本地样例或模拟返回，不需要真实 Get笔记凭证。可加 `-S` 禁用第三方包，检查本地核心是否仍可用。

测试通过不证明模型实际整理质量。规则或模式变化应另外阅读代表性原文与成稿，核对数字、限定条件、人物归属与遗漏情况。
