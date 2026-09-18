# Computer-Use Automation System — Design & Strategy

**Working document.** This captures how we understand the assignment, the reasoning behind our approach, and our step-by-step plan. It is the source of truth we track against, and the raw material for the final `/REPORT.md`.

---

## 1. The problem, restated

interface.ai builds AI agents for banks and credit unions. Most of the back-office software those agents must operate — core banking screens, servicing tools, admin consoles — has **no API**. The only way in is to drive the UI the way a human operator would.

This project is the **backend integration layer that gives those agents hands**: a system that

1. uses an LLM to *discover* how to accomplish a task against a live surface,
2. *records* that successful run as a structured, reusable artifact,
3. *replays* it deterministically — no LLM in the loop — so the agent can invoke it reliably and cheaply in production.

Three properties of the real environment shape every decision we make:

1. **Stable UIs, but real runtime errors.** Enterprise UIs change slowly (which is what makes record-once/replay-many viable), but a replay must handle the runtime conditions that legitimately occur: validation errors, "record not found", permission denials, unexpected dialogs, session timeouts, slow loads, outright errors. A happy-path-only capability is useless in production.
2. **Heterogeneous, often legacy surfaces.** Modern web, legacy web (server-rendered, framesets, deeply nested tables, non-semantic markup, no test IDs), even native desktop. We cannot assume a clean DOM.
3. **Multi-tenant at scale.** Hundreds of institutions, thousands of app instances, many running the same vendor product configured, branded, and versioned differently. Automation should generalize — or degrade gracefully — rather than being rebuilt per tenant.

## 2. Our thesis (the core insight)

> **The model discovers. The artifact becomes a reusable capability. Deterministic replay is how the agent invokes it in production.**

One sentence to keep in your head through the whole build:

> **The LLM appears exactly once — during discovery. After that it leaves the loop.**

Everything below exists to protect that separation. It is the entire point of the assignment, and it is what separates a real design from "an AI wrapper around Playwright."

## 3. What we're optimizing for

The evaluation weighs, in rough order: **system design** (artifact schema + replay contract are central) → **correctness of the core loop** → **robustness & error handling** → **human-in-the-loop escalation** → **generalization** → **safety**. Feature breadth and premature infrastructure are explicitly *not* rewarded.

Therefore we spend depth on the three **load-bearing pieces**, and deliberately keep everything else thin-but-real:

1. **Artifact schema** — the schema and the replay contract.
2. **Deterministic replay + error taxonomy** — separating expected business outcomes from recoverable conditions from hard failures.
3. **Human-in-the-loop handoff** — a real control-transfer mechanism, not a TODO.

## 4. Key decisions & rationale

### 4.1 Why those three pieces are load-bearing

- **Artifact schema** is where "one-time intelligence" becomes "reusable capability." If the schema is wrong, replay has nothing to stand on. It must be *typed, versioned, reviewable*, and — critically — **decoupled from the raw LLM transcript**. We store the distilled steps, not the model's monologue.
- **Replay + error taxonomy** is the production path. The single most common design mistake (called out explicitly in the brief) is conflating a *business outcome* ("no such member") with a *failure*. We build the result contract around three states, and a separate recovery-vs-hard-failure split.
- **Handoff** is where a system that "safely stops when stuck" becomes a system a bank can actually run. It must be a real pause → cede → resume state machine on the *same live session*.

### 4.2 Observation: accessibility tree primary, vision fallback, screenshots as evidence

We observe the surface through **three eyes, each with one job**:

1. **Accessibility tree (primary).** The browser/OS exposes a semantic representation (role, name, value) for screen readers. It is *text* (cheap, works with a text-only model), and it is *more stable than the raw DOM* because it captures what a human "reads" rather than the implementation. This is the right default for legacy apps with no clean DOM and no test IDs.
2. **Vision model (fallback — Kimi K3).** When the accessibility tree is missing or the surface is genuinely visual (canvas, non-semantic UI), we screenshot and let a vision model *understand* the page. This beats OCR because a vision model reads *semantics* ("this is a confirm dialog", "this is a red error"), not just glyphs.
3. **Screenshots (evidence).** On failure we persist a screenshot/DOM snapshot as the "richer signal" the brief requires — for debugging and for human review.

These are additive, not competing.

### 4.3 The critical separation: understanding vs. locating vs. executing

This is the load-bearing distinction of the whole project:

| Phase | Question | Who answers it | Uses |
|-------|----------|----------------|------|
| **Understanding** | "What page am I on? What should I do next?" | LLM (DeepSeek for structured decisions, K3 for visual understanding) | only during **discovery** |
| **Locating** | "Where is the control I need, and how do I point at it robustly?" | the **artifact** (a locator strategy, not a screenshot) | recorded once, used forever |
| **Executing** | "Find it, act on it, verify it worked." | the **replay engine** | **replay**, no LLM |

The trap we refuse to fall into: if replay re-runs the vision model to find elements, then the LLM is back in the loop and "deterministic replay" is a lie. So:

> **K3 sees the UI once (discovery). The artifact records *how to locate* each control. Replay locates by locator, never by looking again.**

### 4.4 Target application: a deliberately hostile local mock

We do **not** automate against a public site (ToS, rate limits, uncontrollable error states). Instead we build a small local mock of a legacy bank back-office app that is *intentionally* hostile: table-based layouts, no test IDs, deeply nested markup, and planted runtime conditions we can trigger on demand:

- a "no such member" lookup (a legitimate **business outcome**),
- a confirmation dialog (a **recoverable condition**),
- a permission-denied state (a **hard failure**).

This lets us *prove* our error taxonomy and produce the "replay that hits an error" evidence the brief explicitly asks for, deterministically.

### 4.5 Architecture: single process, clean modules

The brief explicitly discourages building queues/clusters/multi-tenant plumbing. We keep it one process, six modules with clean boundaries:

```
agent loop  →  artifact model  →  replay engine
                 ↑                    ↑
              safety/guardrails    escalation/handoff
                 └─────── evidence/observability ───────┘
```

### 4.6 Tech stack

- **Language/runtime:** Python 3.12
- **Browser automation:** Playwright (accessibility API + screenshots)
- **Decision LLM:** DeepSeek (structured JSON output for the observe→decide→act loop)
- **Vision LLM:** Kimi K3 (screenshot understanding fallback)
- **Artifact modeling:** Pydantic (typed + versioned by construction)

## 5. Artifact schema (sketch)

The artifact is a **Capability**: a typed, versioned description of one reusable flow.

```
Capability
├─ meta: name, version, description, surface (browser|desktop), created_at
├─ inputs:  [{name, type, required, description}]        # e.g. member_id: str
├─ outputs: [{name, type, source}]                        # e.g. member_name, account_status
├─ checkpoint: how we know the goal was reached           # e.g. "summary panel visible + status == Active"
└─ steps: [
    {
      action: click | type | select | navigate | read | wait
      target: {
        strategy: accessibility | text | css | xpath      # + fallback chain
        value: "role=button name='Search'"
        reasoning: "why this locator was chosen"          # for human review + future repair
      }
      assert: { expect: "...", on_mismatch: ... }         # checkpoint per step, not just the end
      on_error: { outcome: business|recoverable|hard, then: ... }
    }
  ]
```

Design principles (these are the *why*):

1. **Intent, not coordinates.** We record *what* control to act on and *how to point at it*, never absolute pixels/timing. Replay re-resolves the locator each run — deterministic yet robust to benign layout shifts.
2. **Decoupled from the transcript.** The artifact is distilled, typed steps — a human reviewer and a calling agent can both read it, and it survives even if the discovery model changed its mind six times.
3. **Every step carries an assert.** Checkpoints are per-step, not just a final one — we never assume a click worked.
4. **Errors are first-class.** Each step declares how to classify and handle its failure modes, feeding the replay engine's decision tree.

## 6. Deterministic replay + error taxonomy

Replay is **not** blind re-execution; it is per-step *act → assert → branch*.

The result contract has exactly three shapes:

1. **success** — goal reached, typed `outputs` returned.
2. **business_outcome** — a legitimate, expected answer the caller needs ("no such member", "already processed"). Not a crash.
3. **failure** — a hard stop with a debuggable diagnostic: which step, what was expected, what was observed.

Recovery is separated from failure:

- **Recoverable conditions** (dismiss a known interstitial, wait/retry a transient load, re-auth on session timeout) are handled inline with bounded retries.
- **Hard failures** (permission denied, unexpected structure, exhausted retries) stop the run, surface a clear error, and — when appropriate — route to human escalation (§7).

## 7. Human-in-the-loop handoff

A real control-transfer state machine on the **same live session**:

```
AUTOMATION ──stuck/risky/irreversible──▶ PAUSED ──operator takes over──▶ HUMAN
    ▲                                                                     │
    └──────────────────── resume after operator signals ───────────────────┘
```

- **Detect & route:** identify a blocked state, raise an intervention request carrying the capability/goal, the current step, a screenshot/DOM snapshot, and *why* it stopped.
- **Take control:** the operator drives the *live* session (not a fresh one), performs the manual steps, then signals resume.
- **Resume:** the run continues from where it paused; the human's actions are recorded into the evidence log.
- **Control ownership:** the system can always answer "who is in control, and who should be."

The operator console is deliberately mocked, but the pause/cede/resume mechanism and the control-ownership model are real.

## 8. Safety & guardrails

- **Explicit, configurable allowlist** of permitted domains/routes and permitted action types. The agent cannot act outside it — this is enforced in the agent loop *and* re-checked in replay.
- **Risky/irreversible actions** (submit, delete, approve) are classified and gated — either blocked outright or routed to human escalation.
- **Data handling:** regulated financial data is never persisted into artifacts or logs; extracted values are typed and only returned to the caller, never dumped into evidence.

## 9. Heterogeneity & multi-tenant (design only — not built)

- **Surface abstraction:** the artifact schema is surface-agnostic. `action` is a small typed vocabulary (click/type/select/read/wait/assert); `target.strategy` is the only place a concrete surface leaks in. A desktop surface is a new `strategy` + a new driver behind the same engine, not a rewrite.
- **Cross-tenant reuse:** because we record *intent + locator strategy* (not coordinates), an artifact recorded on one institution's instance of a vendor product can be applied to a second, differently-branded instance — with per-variant overrides where a route or label differs. This is the "canonicalize /12345 → /:id" stretch goal, and it falls out of the schema for free.

## 10. Implementation roadmap

- [ ] **Phase 1 — mock app.** Build the hostile local legacy app with planted error/outcome states.
- [ ] **Phase 2 — agent loop (discovery).** Playwright + accessibility tree (primary) + K3 vision (fallback) + DeepSeek structured decisions; one real end-to-end run against the mock.
- [ ] **Phase 3 — artifact.** Pydantic schema + serialize the discovery run into a Capability; reviewability + versioning.
- [ ] **Phase 4 — replay.** Deterministic act→assert→branch engine; the three-state result contract; error/recovery handling.
- [ ] **Phase 5 — safety + escalation.** Allowlist enforcement; pause/cede/resume handoff state machine (mocked operator UI).
- [ ] **Phase 6 — evidence.** `/evidence/` with a saved artifact, a discovery log, and a replay log — including one replay that hits an error state.
- [ ] **Phase 7 — REPORT.md.** Distill this document into the seven mandated headings.

## 11. Decision log / status

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-09-17 | Observation = accessibility tree primary, Kimi K3 vision fallback, screenshots as evidence | text-primary is cheap + stable on legacy; vision covers non-semantic surfaces; screenshots satisfy the "richer signal" requirement |
| 2026-09-17 | Target = local hostile mock, not a public site | full control over error states to *prove* the taxonomy; no ToS/rate-limit risk |
| 2026-09-17 | Single process, six modules; no infra | brief explicitly discourages premature scaling |
| 2026-09-17 | Stack: Python + Playwright + DeepSeek (decisions) + K3 (vision) + Pydantic | maturity, our familiarity, and K3 solves the vision gap without external OCR |
| 2026-09-17 | K3 verified: endpoint `api.moonshot.cn`, model `kimi-k3`, vision works (read "12345" from an image), reasoning model (`reasoning_content` + `content`) | vision fallback is now grounded, not assumed |

---

*"Cut depth, not whole capabilities. Prefer a thin-but-real version of every core requirement."*
