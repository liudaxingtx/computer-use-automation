# REPORT — Computer-Use Automation System

> Final design write-up for the interface.ai take-home. Implementation is complete and backed by real runs under `/evidence/` and `/artifacts/`.

## 1. Architecture

Back-office software has no API — the only way in is to drive the UI like a human. This is the integration layer that does that: **an LLM discovers a task once, the successful run is distilled into a typed artifact, and a deterministic engine replays it with no LLM in the loop.**

The load-bearing invariant: **the LLM appears exactly once — during discovery — then leaves the loop.** Everything else protects that separation.

```
ENGINE (agent/)
  DISCOVERY (LLM, once)   observe → decide → act   →   distills to a Capability
  REPLAY (deterministic, forever)   act → assert → branch, three-state result
        │
  ADMIN console (/) — manage/create      USER runner (/user) — just use tasks
```

Data flow is **discover → record → replay**. Three deliberate trade-offs:

- **Accessibility tree first** — text survives branding/version drift; a vision model is the fallback; screenshots are failure evidence only.
- **Artifact decoupled from transcript** — the LLM monologue is thrown away; a typed, diffable description persists.
- **Single process, concurrent control plane** — a `ThreadingHTTPServer` runs AI optimize/discovery as background jobs so a slow LLM never blocks live calls.

## 2. Artifact schema

The artifact is a **Capability** — a typed, versioned, reviewable description of one flow:

```
Capability
├─ meta (name, version, domain, start_url)
├─ inputs / outputs                          # typed specs
├─ checkpoint_text                           # how success is recognized
├─ business_outcomes / failure_patterns      # deterministic text signals
├─ examples                                  # runnable input→result cases
└─ steps: [{ action, target{strategy, role, name, ordinal}, value, assertion, on_error }]
```

Six principles: **intent not coordinates** (locate by role+name, never pixels — one vendor's instance transfers to another's); **decoupled from transcript**; **every step asserts**; **errors are first-class** (`business_outcomes` = legitimate answers, `failure_patterns` = hard stops); **human-manageable** (diffable JSON); **customer data encrypted at rest** (AES-256-GCM per tenant).

Every task declares a **standardized output contract** — the JSON it returns per case: **success** → typed fields (`{"name": …, "status": …}`); **business_outcome** → `{"outcome": "<label>"}`; **failure** → no JSON. A caller branches on the shape without inspecting the page.

**Runnable examples** are editable input→result cases (one-click fill → Execute to verify); each may carry an **`expect` output assertion** that hard-gates the result (see §3).

## 3. Determinism & error handling

Replay is **act → assert → branch**, classifying every result into one of three states:

- **success** — goal reached, typed outputs extracted.
- **business_outcome** — a legitimate expected answer (e.g. "no such member"); a page-returned error message is a *correct* answer, not a crash.
- **failure** — a mid-run error-out that produced no JSON.

From the caller's view this collapses to a binary: **succeeded = success + business_outcome** (both return JSON); only `failure` (no JSON) is a real failure.

**`expect` tightens it further.** A run carrying an output assertion must *satisfy* it or it is reclassified **failure** — even if the checkpoint matched. A well-formed JSON with the wrong fields means the task itself is broken — the one failure a bare "did it return JSON?" check can't catch. Enforced server-side, so stats / task health / replay all see it as a true failure.

Robustness: locator strategy is an enum (accessibility → text/css/xpath); recoverable vs hard failures are separated (`on_error.retry`); SPA async-render is handled by a bounded checkpoint poll; reasoning-model output drift self-heals.

### Observability, optimization, and task health — one loop

Every invocation is recorded and re-runnable. The admin console aggregates per-task rates over *user* calls; failures (the only no-JSON case) show the **encrypted** input + a one-click **Replay**. A failing run is fixed conversationally with **AI optimize** (plain-English → validated structured patch → version auto-bumped), run as a background job. Each task carries a three-state health signal — **verified / unverified / error** — anchored on the last optimize; a broken task is highlighted in admin and **hidden from users** until re-verified.

## 4. Heterogeneity & multi-tenant

The schema is surface-agnostic: `action` is a small typed vocabulary and `target.strategy` is the only place a surface leaks in, so a desktop driver or a legacy frameset app is a new strategy, not a rewrite. Recording intent + locator strategy (not coordinates) means one tenant's artifact applies to a differently-branded instance. Known boundary: canvas/image-only surfaces need coordinate clicking — the `visual` strategy is reserved but not built, since the target apps are DOM-based.

## 5. Escalation & handoff

A real control-transfer state machine on the same live session: `AUTOMATION → PAUSED → HUMAN → (resume)`. The system detects a blocked state, hands the live session to an operator, and resumes where it paused while recording the human's actions — always able to answer "who is in control."

## 6. Safety

Three layers: an **allowlist** (domains + action types, enforced in discovery *and* replay); **action gating** (risky/irreversible actions blocked or escalated); and **data handling** (inputs AES-256-GCM encrypted at rest with per-tenant keys, decrypted only at the moment of use, never revealed in the report). Threat model is accidental mis-action, not a determined adversary.

## 7. Cuts

Depth over breadth: no queues/clusters/multi-tenant plumbing, no coordinate clicking, no general commercial sites (proved against a hostile local mock + SauceDemo / The Internet). Scoped out with known paths: capability dedup, automatic error-state discovery, generalized output extraction.

---

See **[README.md](README.md)** for the full runbook — install, demo path, the mock's multi-modal inputs, and the statistics / AI-optimize loop in detail.
