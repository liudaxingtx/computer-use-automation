# REPORT — Computer-Use Automation System

> Final submission write-up for the interface.ai take-home. The implementation is complete and backed by real runs under `/evidence/` and `/artifacts/`.

## 1. Architecture

interface.ai's agents must drive back-office software that has **no API** — the only way in is to operate the UI like a human. This project is the backend integration layer that gives those agents hands: **an LLM discovers how to accomplish a task once, the successful run is distilled into a typed, reusable artifact, and a deterministic engine replays it with no LLM in the loop.**

The whole design hangs on one invariant: **the LLM appears exactly once, during discovery, then leaves the loop.** Everything else protects that separation — it is what makes this a design rather than "a Playwright wrapper."

The system is a single process with clean module boundaries, in three blocks that share one engine:

```
                 ┌───────────────────────────────────────────────┐
                 │  ENGINE  (agent/)                             │
   new task  ──► │  DISCOVERY (LLM, once)   observe→decide→act   │
                 │       ▼  distills to a Capability artifact    │
                 │  REPLAY   (deterministic, forever)            │
                 │       act→assert→branch,  three-state result  │
                 └───────────────┬───────────────────────────────┘
          ┌──────────────────────┴──────────────────────┐
   ┌──────▼───────┐                            ┌────────▼────────┐
   │ ADMIN console │  (/)                      │ USER runner    │ (/user)
   │ manage/create │                            │ just use tasks │
   └──────────────┘                            └─────────────────┘
```

Key decisions and trade-offs:

- **Observation uses three eyes, each with one job.** The **accessibility tree** is primary (text, cheap, and more stable than raw DOM on legacy apps); a **vision model** is the fallback for non-semantic surfaces; **screenshots** are captured only as evidence on failure. Trade-off: we bias toward the accessibility tree even though screenshot+coordinates would look more "general", because the tree survives branding/version drift that raw pixels don't.
- **The artifact is decoupled from the transcript.** The raw LLM monologue is thrown away; what persists is a distilled, typed description a human can review and diff.
- **Single process, sync driver.** Simplicity over service sprawl — the brief explicitly discourages premature infrastructure (§7 Cuts). The seams (a `target.strategy` enum, a driver behind `replay`) are where a future service/queue boundary would go.

## 2. Artifact schema

The artifact is a **Capability**: a typed, versioned, reviewable description of one reusable flow. It is the focal point of the design, so it is shaped deliberately.

```
Capability
├─ meta: name, version, description, domain, start_url
├─ inputs / outputs                     # typed specs, e.g. member_id: str
├─ checkpoint + checkpoint_text         # how we know the goal was reached
├─ business_outcomes / failure_patterns # deterministic text signals for classification
└─ steps: [{ action, target{strategy, role, name, name_ordinal, ordinal},
             value, assertion, on_error }]
```

Six principles govern it:

1. **Intent, not coordinates.** We record *how to locate* a control (role + name), never pixels. A capability recorded on one institution's instance of a vendor product applies to a second, differently-branded instance.
2. **Decoupled from the transcript.** Distilled, typed steps survive even if the model changed its mind mid-run.
3. **Every step carries an assertion.** A click's success is never assumed.
4. **Errors are first-class.** Each step declares its failure modes; the capability carries explicit `business_outcomes` (legitimate answers) separate from `failure_patterns` (hard stops).
5. **Human-manageable.** Plain diffable JSON — review, edit, and version it like code (a CLI exposes `edit` / `bump` / `verify` for exactly this).
6. **Customer data encrypted at rest.** Input values are AES-256-GCM encrypted with a per-tenant key before they touch disk; the locator strategy stays plaintext so the artifact remains reviewable.

The schema is the contract the calling agent sees, not just a step list: typed inputs it supplies, typed outputs it gets back, and a checkpoint that defines success.

## 3. Determinism & error handling

Replay is **act → assert → branch**, never blind re-execution. It re-resolves each locator against the live page, acts, then classifies the result by deterministic text signals — never by asking a model to look again.

The result contract has exactly three states: **success** (goal reached, typed outputs extracted), **business_outcome** (a legitimate expected answer like "no such member" — *not* a crash), and **failure** (a hard stop with a debuggable diagnostic: which step, expected vs observed). This is the load-bearing distinction the brief calls out, and it is proven, not asserted — `/evidence/runs/` carries real runs for all three states.

Robustness comes from four concrete mechanisms:

- **Locator strategy is an enum.** Accessibility (role+name) is primary; `name_ordinal` disambiguates repeated same-name controls (a real storefront has six identical "Add to cart" buttons); `ordinal` falls back for empty-name controls; text/css/xpath are the last resort. The strategy is chosen *at record time* with reasoning captured on the target.
- **Recoverable vs. hard failure are separated.** Recoverable conditions (a transient load, a known interstitial) retry with a bounded count via `on_error.then = retry`; hard failures stop and surface a diagnostic.
- **Async-render (SPA) waiting.** Real sites render results *after* navigation settles. Replay polls for the checkpoint (or an outcome) for a bounded window before declaring failure — this is the difference the mock alone couldn't expose, and it is what made the SauceDemo checkout replay reliable.
- **Reasoning-model output drift.** Reasoning LLMs can spend their whole budget on a monologue and return empty `content`; a generous budget, a `reasoning_content` fallback, and a retry make the discovery loop self-heal.

Secondary (UI drift) is handled by re-resolving locators at replay time and asserting after every step; the artifact-edit CLI is the repair surface when a target genuinely changes.

## 4. Heterogeneity & multi-tenant

The schema is **surface-agnostic**: `action` is a small typed vocabulary (`click` / `type` / `select` / `navigate` / `wait`), and `target.strategy` is the only place a concrete surface leaks in. A desktop surface is a new strategy plus a driver behind the same engine — not a rewrite. A legacy web app (framesets, nested tables, no test IDs) is the same engine pointed at a messier tree.

Because we record **intent + locator strategy** (not coordinates), an artifact recorded on one tenant's instance of a vendor product applies to a second, differently-branded instance, with per-variant overrides where a route or label differs. This falls out of the schema for free. Per-tenant/version drift is the one place that genuinely needs the human repair loop: telemetry surfaces which (capability, version) pairs are failing, and the `edit`/`bump`/`verify` CLI is the fix path.

**Known boundary with a known fix — relative/coordinate clicking.** Some legacy surfaces are canvas- or image-only: no DOM, no accessibility tree, nothing to locate by role+name. We reserve a `visual` locator strategy in the schema and the vision fallback already *describes* such screens (including a suggested click point), but there is no `click_at` action yet. The concrete plan: record the click point as an offset from a detected landmark (never raw pixels), re-locate the landmark with the vision model at replay time, then `page.mouse.click(x, y)`. It re-introduces a vision call into the replay path for those rare steps, which is why it's reserved, opt-in, and kept out of the common path rather than shipped speculatively.

## 5. Escalation & handoff

A real control-transfer state machine on the **same live session**:

```
AUTOMATION ──stuck/risky/irreversible──▶ PAUSED ──operator takes over──▶ HUMAN
    ▲                                                                     │
    └──────────────── resume after operator signals ──────────────────────┘
```

The system detects a blocked state, raises an intervention request (capability, step, screenshot, why it stopped), lets the operator drive the *live* session, then resumes from where it paused while recording the human's actions. The system can always answer "who is in control." The operator console is mocked (a clean seam), but the pause/cede/resume mechanism and control-ownership model are real — a hard failure demonstrably pauses to a human and resumes afterward.

## 6. Safety

Three layers. **Allowlist:** permitted domains/routes and action types, enforced in the agent loop *and* re-checked in replay — the agent cannot act outside it. **Action gating:** risky/irreversible actions (submit, delete, approve) are classified and blocked or routed to escalation. **Data handling:** customer-entered values are AES-256-GCM encrypted with a per-tenant key before writing to an artifact or run log, decrypted only at the moment of use; extracted outputs are returned to the caller, never persisted. Keys come from the environment, never from git.

The limits are honest: the allowlist is explicit but finite (the threat model is accidental mis-action, not a determined adversary), and the statistics report deliberately shows a failed run's input as ciphertext (`enc:…`) — an operator can replay it, but never read a customer's raw value from the report.

## 7. Cuts

Depth over breadth, deliberately. **No queues, clusters, or multi-tenant plumbing** (the brief discourages premature infrastructure); **no coordinate/visual clicking** (see §4 — a documented boundary with a known fix, not a shipped capability); **no general commercial sites** (we prove the error taxonomy against a deliberately-hostile local mock *and* two automation-friendly public test sites — SauceDemo and The Internet — rather than targets that sit behind ToS/CAPTCHA/WAF). Each cut trades feature breadth for real depth on the load-bearing pieces — artifact schema, deterministic replay with an error taxonomy, and human handoff — plus a working observability/repair loop.

Next with more time: canonicalization (`/item/12345` → `/item/:id`) to collapse parameter variants, cross-tenant override demonstration, and the coordinate-click fallback from §4.

---

## Appendix — how to run & verify

**1. Demo path (discover → replay).** Boot the mock and dashboard, then:

```bash
# discover a goal once (real LLM run, evidence lands in evidence/)
.venv/bin/python -m agent.main --task "Look up member 1001 and read their balance"

# replay the recorded artifact deterministically — no LLM in the loop
.venv/bin/python -m agent.cli verify lookup_member --input member_id=1001
```

**2. Inspect the evidence.** `/evidence/` holds a saved example artifact, raw discovery transcripts, and append-only replay run logs (inputs encrypted at rest) covering all three result states; `/artifacts/` holds the full capability catalog (version-controlled).

**3. Statistics report.** Open `http://localhost:8123/` → **Statistics**: per-task success/business/failure rates, a run ledger (successes show timing + extracted numbers, failures show the *encrypted* input), and one-click **Replay** of any recorded invocation.
