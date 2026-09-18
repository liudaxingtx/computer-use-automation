# 计算机使用自动化系统（中文版 · 仅本地）

> interface.ai —— 工程 take-home 项目。本文件是 README.md 的中文对照，只留在本地。

一个给 AI agent "装上手"的后端集成层：LLM 驱动系统，发现怎么操作一个 legacy（无 API）应用，把成功运行录成结构化 artifact，再确定性回放——决策循环里没有 LLM——让 agent 在生产里可靠、廉价地调用。

## 仓库结构

| 路径 | 用途 |
|------|------|
| `DESIGN.md` | **工作设计记录** —— 我们对问题的理解、每个决策背后的理由、可追踪的实施计划 |
| `REPORT.md` | 最终提交文档（7 个规定标题），最后从 DESIGN.md 提炼 |
| `src/` | 实现（agent loop、artifact model、replay engine、safety、escalation、evidence） |
| `evidence/` | 真实 discovery run 和 replay run 的 artifact + 日志 |
| `mock-app/` | 故意难啃的本地 legacy 银行后台替代品 |

## 状态

**Phase 0 —— 设计与策略**（当前）。见 DESIGN.md §10 的 roadmap。

## 一句话核心

> **模型负责发现。artifact 变成可复用能力。确定性回放是 agent 在生产里调用它的方式。**

LLM 只出现一次——在 discovery 阶段。之后退出循环，学到的东西变成一个 agent 不用重新推理 UI 就能调用的能力。

完整推理：见 DESIGN.md。
