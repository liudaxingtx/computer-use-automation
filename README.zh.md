# 计算机使用自动化系统（中文版 · 仅本地）

> interface.ai —— 工程 take-home 项目。本文件是 README.md 的中文对照，只留在本地，交作业前隐藏。

一个给 AI agent "装上手"的集成层：针对**没有 API** 的 legacy 软件，LLM 驱动系统**发现**怎么操作 UI，把成功运行录成结构化 artifact，再**确定性回放**——决策循环里**没有 LLM**——让 agent 在生产里可靠、廉价地调用。

## 问题 → 方案

interface.ai 的 agent 要操作后台系统（银行/保险/医疗），唯一的入口是网页 UI，只能像人一样驱动浏览器。每点一下都问一次模型的朴素做法：慢、贵、不确定。

本系统反其道：

```
        发现（LLM，只一次）                 回放（确定性，永久）
  ┌──────────────────────────┐  蒸馏  ┌──────────────────────────────┐
  │ observe → decide → act   │ ─────► │ act → assert → branch        │
  │（真实浏览器会话）           │        │（类型化 Capability artifact）  │
  └──────────────────────────┘        └──────────────────────────────┘
       "这活儿怎么做？"                     "按这些输入，执行它"
```

**模型负责发现，artifact 负责回放。** LLM 只在 discovery 阶段出现一次；学到的东西变成一个可版本化、可审查的 *Capability*。生产调用用纯确定性代码回放它——没有模型、没有推理、没有成本。

## 快速开始

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && playwright install chromium
cp .env.example .env        # 填 DECISION_LLM_API_KEY 与 VISION_LLM_API_KEY（可选，仅录制新 task 需要）
python3 mock-app/server.py  # 本地目标应用（legacy 银行 mock，端口 9000）
.venv/bin/python -m dashboard.server   # 控制台，端口 8123
# 打开 http://localhost:8123
```

查看与回放预录制的 task **不需要任何 API key**——回放是确定性代码、无 LLM 参与；只有录制新 task 才需要 key。

## 演示 —— 七个已录制 task

引擎在**两类目标**上验证：一个故意难啃的本地 mock，外加两个真实部署的公网测试站。

| 站点 | Task | 做什么 |
|---|---|---|
| **本地 mock** (`localhost:9000`) | `lookup_member` | 按 ID 查会员详情 |
| | `deactivate_member` | 查 → 看详情 → 停用账户 |
| | `register_operator` | 填并提交注册表单 |
| | `login_operator` | 填并提交登录表单 |
| **SauceDemo**（真实站） | `saucedemo_login` | 登录 Swag Labs 商店 |
| | `saucedemo_checkout` | **登录 → 加购 → 购物车 → 结账 → 完成下单**（11 步） |
| **The Internet**（真实站） | `theinternet_login` | 登录 Secure Area |

`saucedemo_checkout` 是重头戏：对一个**真实 React 商城**发现并回放完整的多步交易流程——逼出了两个 mock 永远不会暴露的真实修复（见下）。

## 工作原理

1. **发现** —— 决策 LLM 看到一个真实浏览器（URL + 可访问性树 + 带编号的控件菜单），一次只决定一步；纯图片界面回退到视觉模型。
2. **录制** —— 成功轨迹被蒸馏成 `Capability`：类型化步骤、定位*策略*（role+name，绝不用像素）、每步断言、一等的错误状态。
3. **回放** —— 确定性引擎重跑 artifact：重新解析每个定位器、执行动作，把结果归入**三态之一**：
   - **success** —— 目标达成，从页面提取结构化输出；
   - **business_outcome** —— 一个合法答案（"查无此人"），不是崩溃；
   - **failure** —— 硬停，带可调试诊断。

三态分类法是设计核心："查无此人"是*有效答案*，不是错误。它在一个专门埋这些坑的 mock 上得到了验证。

## 仓库结构

```
agent/        发现循环 · artifact 模型 · 确定性回放 · 安全 · 加密 · 可观测性
dashboard/    task 管理控制台（http://localhost:8123）
mock-app/     故意难啃的本地 legacy 银行后台替代品
evidence/     真实发现/回放运行的 artifact + 日志（只追加）
scripts/      一键测试、截图/证据生成、artifact 辅助脚本
```

## 功能

- **发现循环** —— 可访问性树观测 + 视觉兜底 + 结构化 LLM 决策。
- **类型化 artifact** —— 可版本化、可审查的 `Capability`（可 diff 的 JSON，不是模型独白）。
- **确定性回放** —— act → assert → branch，带有限重试与硬停升级。
- **结构化数据提取** —— 成功时把结果页读回 JSON（字段名归一化匹配）。
- **一键录制** —— 给 URL + 一句人话，返回录制并验证好的 task（`POST /api/discover`）。
- **可观测性** —— 只追加的运行存储、成功遥测、失败收件箱、replay 出错 → 修复 → 复验闭环。
- **安全** —— 动作白名单（发现和回放都执行）、客户输入 AES-256-GCM 静态加密、人工接管状态机。
- **task 控制台** —— 按域名分组、逐 task 定位器细节 + 截图、Swagger 式调用、调用日志、删除（带确认）。

## 测试

```bash
./scripts/run_tests.sh
```

拉起 mock 并跑真实端到端套件（发现 → artifact → 三态回放 → 安全/加密/接管 → 视觉兜底 → 可观测性）。它会**真实调用 LLM**——这正是重点：真跑，不是 stub。

## 设计要点

每个决策的理由见 [`DESIGN.md`](DESIGN.md)。几个值得点名的：

- **意图而非坐标** —— artifact 记录*如何定位*控件（role+name），A 机构录制的适用于换个皮肤的同款 B 机构。
- **每步都有断言** —— 从不假设点击成功。
- **同名控件消歧** —— 真实商城有六个一模一样的 "Add to cart" 按钮，定位器记录它在同名组内的序号，回放才点得对。
- **异步渲染（SPA）等待** —— 真实站点导航后才渲染结果，回放轮询成功信号再判失败。
- **推理模型输出漂移** —— 决策模型的推理独白可能吃掉输出预算，加大预算 + `reasoning_content` 兜底 + 重试让漂移自愈。

## 文档

| 文档 | 给谁 |
|---|---|
| **[`GETTING_STARTED.md`](GETTING_STARTED.md)** | 跑起来、配置、验证、看做了什么和刻意砍了什么 |
| [`REPORT.md`](REPORT.md) | 正式提交文档（7 个规定标题） |
| [`DESIGN.md`](DESIGN.md) | 完整设计记录——每个决策背后的理由 |
| [`TODO.md`](TODO.md) | 进度追踪 |
