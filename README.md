# transcript-refine-v3

面向中文逐字稿的开源 agent skill。它把本地逐字稿、网页转写或用户提供的原文整理为可验证的成稿，并保留三个明确的交付分支：交流实录稿、复盘纪要稿、学习整理稿。

## 适合什么

- 商务、客户、销售、合作与访谈对话：先确认交付形态，必要时推荐交流实录稿。
- 多人对话：先锁定说话人；无法确认姓名时只使用低置信标签。
- 课程、讲座与知识型长独白：整理为学习材料。

默认交付一份本地 Markdown，不需要 Get笔记账号。Get笔记读写是可选适配器：只有使用者明确请求并配置自己的凭证时才启用。

## 快速开始

```powershell
git clone https://github.com/TANG9410/transcript-refine-v3.git
cd transcript-refine-v3
.\install.ps1 -Target Codex
```

完整安装、Claude 安装和可选 Get笔记配置见 [INSTALL.md](INSTALL.md)。

## 安全与边界

- 不以 AI 摘要替代原始文本事实源。
- 不猜测未锁定的真实姓名。
- 不删除或移动输入文件。
- 不要提交原始录音、客户逐字稿、账号配置、Token 或任何个人数据。

本项目采用 [MIT License](LICENSE)，维护者：唐玮。