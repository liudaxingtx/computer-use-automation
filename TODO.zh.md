# TODO 与进度追踪（中文版）

图例：✅ 已完成 · 🚧 进行中 · ⬜ 未开始

## ✅ 已完成 —— 做了什么、什么时候做的

| 日期 | 事项 |
|------|------|
| 2026-09-17 | Phase 0 —— 设计与策略（`DESIGN.md`、`README.md`、`REPORT.md` 骨架） |
| 2026-09-17 | 中文对照文档（`DESIGN.zh.md`、`README.zh.md`、`REPORT.zh.md`） |
| 2026-09-17 | 仓库初始化 + git 身份（Da Liu / liudaxingtx） |
| 2026-09-17 | K3 API key 验证通过（端点 cn + 视觉 + 推理模型） |
| 2026-09-17 | Phase 1 —— mock 应用（`mock-app/server.py`）：Legacy Member Services，埋了 3 个运行时状态 |
| 2026-09-17 | Phase 2 —— agent loop（`agent/`）：无障碍树观察 + DeepSeek 结构化决策 + K3 视觉兜底；真实端到端 5 步跑通 |
| 2026-09-18 | Phase 3 —— artifact（`agent/artifact.py`）：Pydantic `Capability` schema + `serialize`；类型化/版本化/可评审 JSON |
| 2026-09-18 | Phase 4 —— replay（`agent/replay.py`）：确定性 操作→断言→分支（无 LLM）；三态契约验证通过；ReplayRun 记录 |
| 2026-09-18 | Phase 5 —— 安全 + 升级 + artifact 管理（`agent/safety.py`、`handoff.py`、`crypto.py`、`cli.py`）：allowlist、AES-256-GCM 加密、handoff、CLI list/edit/bump/verify |

## 🎯 路线图

### Phase 1 — Mock 应用（legacy 银行后台替身）
- ✅ 搭难啃的 mock：表格布局、无 test ID、深嵌套标记
- ✅ 埋三个运行时状态 —— "查无此人"（业务结果）、确认弹窗（可恢复）、权限拒绝（硬失败）
- ✅ 接通一个非平凡多步流程（搜索 → 详情 → 操作）

### Phase 2 — Agent loop（discovery）
- ✅ Playwright + 无障碍树观察（主）
- ✅ Kimi K3 视觉兜底（无语义界面）
- ✅ DeepSeek 结构化决策（observe → decide → act）
- ✅ 对 mock 跑通一次真实端到端 discovery run

### Phase 3 — Artifact
- ✅ Pydantic artifact schema（Capability）
- ✅ 把 discovery run 序列化成 Capability
- ✅ 版本化 + 可评审

### Phase 4 — 确定性回放
- ✅ 回放引擎：操作 → 断言 → 分支
- ✅ 三态结果契约（success / business_outcome / failure）
- ✅ 可恢复 vs 硬失败处理（带边界重试、硬停）+ 空 name 的 ordinal fallback

### Phase 5 — 安全 + 升级 + artifact 管理
- ✅ 可配置 allowlist（域名/路由 + 动作类型），loop 和回放里都强制
- ✅ live session 上 暂停/让出/恢复 交接状态机
- ✅ artifact 管理 CLI：列表 / 改定位 / bump 版本 / verify（dry-run 回放）
- ✅ 静态加密（AES-256-GCM step 值 + per-tenant 密钥，用时解密）

### Phase 6 — 证据 & 可观测性
- ⬜ `/evidence/`：保存的 artifact + discovery 日志 + replay 日志（含一次命中错误）
- ⬜ 成功率统计、失败案例库、以及 回放错误→修复→复查 的闭环

### Phase 7 — REPORT.md
- ⬜ 把 `DESIGN.md` 提炼成七个规定标题

## 🔜 下一步

**Phase 6 —— 证据 & 可观测性。**

1. `/evidence/`：一个保存的 artifact、一份真实 discovery 日志、几份 replay 日志——含一次命中错误状态的回放
2. 成功率统计 + 失败案例库 + 回放错误→修复→复查 的闭环

## 📦 交作业前

- [ ] 在 `.gitignore` 里重新隐藏 `*.zh.md`，并从仓库移除
- [ ] 确认没有密钥（`.env`）被提交
- [ ] push 到公开 GitHub 仓库（liudaxingtx）
- [ ] 把仓库链接发邮件到 assignments@interface.ai
