# 报告 — 计算机使用自动化系统（中文版 · 仅本地）

> 最终提交文档。从 `DESIGN.md` 提炼；实现已完成到 Phase 6，有 `/evidence/` 里的真实运行背书。

## 1. 架构

interface.ai 的 agent 必须驱动那些**没有 API** 的后台软件——唯一入口就是像人一样操作 UI。这个项目就是「给 agent 装上手的后端集成层」：**LLM 发现一次怎么完成任务，把成功的运行提炼成带类型、可复用的 artifact，再用确定性引擎无 LLM 地回放它。**

整个设计挂在一个不变式上：**LLM 只出现一次——在 discovery 阶段——之后就退出循环。** 其余一切都在守护这个分离；这正是「真正的设计」和「套了 Playwright 的 AI 包装」的分水岭。

系统是单进程、模块边界清晰：

```
agent loop  →  artifact model  →  replay engine
                 ↑                    ↑
              safety/guardrails    escalation/handoff
                 └─────── evidence/observability ───────┘
```

观察用三只眼睛，各管一件事：**无障碍树**（主——文本、便宜、在 legacy 应用上比原始 DOM 更稳）、**视觉模型**（Kimi K3，无语义界面兜底）、**截图**（失败时的证据）。决策由 DeepSeek 用结构化 JSON 输出完成。

## 2. Artifact schema

artifact 是一个 **Capability**：一个可复用流程的、带类型、带版本、可评审的描述。它记录*做什么*（步骤）、*怎么指到*每个控件（定位策略，绝不是像素）、*每步之后期望什么*（断言）、以及*怎么分类*失败（错误处理）。它刻意和原始 LLM 对话记录解耦。

```
Capability
├─ meta: name, version, description, surface, created_at
├─ inputs / outputs                     # 带类型的规格，如 member_id: str
├─ checkpoint + checkpoint_text         # 怎么知道目标达成了
├─ business_outcomes / failure_patterns # 分类用的确定性文本信号
└─ steps: [{ action, target{strategy, role, name, ordinal, reasoning},
             value, assertion, on_error }]
```

六条原则：①**意图，不是坐标**——记的是「怎么定位」，绝不记像素；②**和对话记录解耦**——提炼后的带类型步骤，哪怕模型中途反悔也不受影响；③**每步都带断言**——绝不假设点一下就成了；④**错误是一等公民**——每步声明它的失败模式；⑤**人可管理**——纯可 diff 的 JSON，像代码一样评审/编辑/版本；⑥**客户数据静态加密**——输入值用 per-tenant 密钥 AES-256-GCM 加密，回放时才解密；定位策略保持明文，所以 artifact 依然可评审。

## 3. 确定性、定位与稳健性策略

回放是 **操作 → 断言 → 分支**，绝不是盲执行。它对活页面重新解析每个定位，用确定性文本信号分类结果——绝不叫模型再看一眼。

结果契约恰好三种状态：**success**（目标达成、返回带类型输出）、**business_outcome**（合法预期答案，如「查无此人」——*不是*崩溃）、**failure**（硬停 + 可调试诊断：哪一步、期望 vs 实际）。恢复和失败分开：可恢复条件（关插页、重试瞬时加载）内联带边界重试；硬失败停下、转升级。这个三态契约是**被证明的、不是嘴上说的**——`/evidence/` 里对故意难啃的 mock 有全部三种状态的真实回放。

定位策略：**accessibility（role+name）为主**，空 name 有 ordinal fallback，另有 text/css/xpath。一个刻意的边界：**我们不落地坐标/视觉点击。** schema 预留了 `visual` 策略，K3 视觉兜底也已经能*定位*非语义控件（返回坐标）——但 act 词汇表里没有坐标动作，所以纯图形界面（canvas、图片验证码）是*看得懂*但还*点不动*。我们把这条记录为「已知边界 + 已知解法」（一个 template-match 或归一化坐标的 `visual` 定位）而不是去实现它：它违背「意图不坐标」原则，且只对最罕见的界面才需要。

## 4. 异构与多租户

artifact schema 是**与具体界面无关**的：`action` 是一小组带类型动词（click/type/select/navigate/wait），`target.strategy` 是唯一漏出具体界面的地方——桌面界面 = 新 strategy + 一个驱动，同一引擎后面，不是重写。因为我们记的是「意图 + 定位策略」（不是坐标），一家机构实例上录的 artifact 能套到第二个、品牌不同的实例上，路由或标签不同处用 per-variant override。这从 schema 里自然长出来，不用额外做。

## 5. 升级与交接

同一个 **live session** 上真实的控制转移状态机：

```
AUTOMATION ──卡住/危险/不可逆──▶ PAUSED ──操作员接管──▶ HUMAN
    ▲                                                       │
    └────────────── 操作员信号恢复后继续 ────────────────────┘
```

系统识别阻塞状态、发起干预请求（能力、步骤、截图、为什么停）、让操作员驱动*同一个* live session、再从暂停处恢复并把人的动作记进日志。系统随时能回答「谁在控制」。操作员控制台故意 mock，但暂停/让出/恢复机制和控制归属模型是真的——硬失败会实打实地暂停给人类、之后恢复。

## 6. 安全

三层。**Allowlist：** 允许的域名/路由和动作类型，agent loop 里强制、回放时再查一遍——agent 不能越界。**动作设卡：** 危险/不可逆动作（提交、删除、审批）分类，要么拦要么转升级。**数据处置：** 客户输入值写入 artifact 或 ReplayRun 前用 per-tenant 密钥 AES-256-GCM 加密、用时才解密；提取出的输出只返回给调用方、绝不持久化。密钥来自环境，绝不进 git。

## 7. 砍掉了什么

要深度不要广度，刻意砍：**不建队列/集群/多租户管道**（题目不建议过早基础设施）；**不对公开网站自动化**（ToS 和状态不可控——我们用本地难啃 mock 证明错误分类）；**不落地坐标/视觉点击**（见 §3——是「已知边界 + 已知解法」，不是已交付能力）。每一刀都用功能广度换三块承重墙（artifact schema、确定性回放 + 错误分类、人工交接）以及可观测/修复闭环上的真实深度。

---

## 附录 — 怎么运行 & 验收

从快到深，三条验收路径：

**1. 一键测试。** `./scripts/run_tests.sh` 拉起 mock，然后跑真实端到端测试：discovery（DeepSeek 决策）→ artifact 序列化 → 确定性回放（三种结果状态全覆盖）→ 安全/加密/交接 → K3 视觉兜底 → 可观测性。它会真实调用 LLM、花一点点 token——这正是重点：是真实 run，不是 mock。

**2. 查看证据。** `evidence/` 放了一次真实运行的产物：提炼后的 Capability（`artifact_deactivate_member.json`）、原始 discovery 记录、三份 replay（`success`、`business_outcome`「查无此人」、`failure`「权限拒绝」）、以及失败截图。读这些，能看到三态契约和静态加密在真实数据上工作。

**3. 自己上手走一遍。** 让 mock 跑着（`python3 mock-app/server.py`）：

- 发现：`.venv/bin/python -m agent.main --task "Search for member 1001, view their detail, then deactivate the account."`
- 管理 artifact：`.venv/bin/python -m agent.cli list | telemetry | failures | replay-case <id> | resolve <id>`
- 复现修复闭环：`verify` → `edit --step 2 --new-name WRONG`（失败）→ `edit --step 2 --new-name SEARCH` → `bump` → `verify`（通过）

README 里有完整操作手册。
