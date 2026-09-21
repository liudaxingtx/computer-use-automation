# REPORT — Computer-Use Automation System

> Design write-up for the interface.ai take-home. Implementation is complete and backed by real runs under `/evidence/` and `/artifacts/`.

## 1. Architecture

Back-office software exposes no API — the only way in is to drive the UI like a human. This system is the integration layer that turns that into a safe, callable product: **an LLM discovers a flow once, the successful run is distilled into a typed artifact, and a deterministic engine replays it with no model in the loop.**

The load-bearing invariant: **the LLM appears exactly once — during authoring — then leaves the loop.** Reliability, safety, and auditability all follow from that separation.

```
ENGINE (agent/)
  DISCOVERY (LLM, once)   observe → decide → act   →   distills to a Capability
  REPLAY (deterministic, forever)   act → assert → branch, three-state result
        │
  ADMIN console (/) — manage/create      USER runner (/user) — just use tasks
```

**Three roles over one engine.** The **admin console** is an AI-assisted control plane to add (discover), edit (optimize / bind inputs / rename), and delete tasks, and to watch the failure inbox and statistics. The **user runner** is a login-gated surface exposing only tasks not marked broken; each user has a private folder, their legacy credential is injected from it (never a shared account), and inputs bound to their own data (e.g. member id ← employee id) are auto-filled, never typed. The **audit** records every invocation append-only with the caller's identity; a hard failure flips a task to `error`, hides it from users, and surfaces it in the admin console for repair.

**Target.** The stand-in for the real thing is a deliberately-hostile local mock — a 1998-era banking portal with table-based layouts, terse labels, and no test IDs — plus the public SauceDemo / The Internet sandboxes for real-world verification. A hostile mock is the right proxy because it forces the load-bearing problems (a multi-step search → detail → action flow, and multi-modal outcomes where the *same* input returns *different* results) to be solved for real, rather than hidden behind a clean modern DOM.

**Runtime & frameworks.** Python 3.12, Playwright (Chromium), Pydantic v2, the stdlib `http.server`, `httpx`, and `cryptography`. Playwright is chosen over Selenium/Puppeteer for its first-class accessibility-tree API (`get_by_role`/`get_by_name`), which is what makes replay *semantic* rather than coordinate-based, and over screenshot/CUA SDKs because driving the DOM through the a11y tree is deterministic and cheap — a vision model is only the fallback for image-only surfaces. Pydantic gives one typed, validated, diffable schema that doubles as the artifact format *and* the LLM's output contract. The web surface is a thin stdlib `ThreadingHTTPServer` — no framework — because the value is the engine, not the HTTP layer.

**LLM roles & agent loop.** Two provider-agnostic roles talk to any OpenAI-compatible endpoint through one thin `httpx` helper — no vendor SDK. A **decision** model drives the `observe → decide → act` loop and returns one strict JSON action per turn (tolerantly parsed; reasoning-model `reasoning_content` is separated from the answer); a **vision** model is the fallback when the accessibility tree is empty. Defaults are DeepSeek for decision and Moonshot Kimi for vision, but nothing is hard-wired — `DECISION_LLM_*` / `VISION_LLM_*` env vars repoint each role. Structuring the loop as a strict action-per-turn JSON — not a free-form agent — is what makes its output distillable into a deterministic artifact.

**Boundaries.** One process, synchronous execution, no queue. Slow work (AI optimize, discovery) runs as background threads so a live call is never blocked. This is deliberately simpler than a worker/queue topology: the assignment is about deterministic replay, not scale, and a queue adds coordination without changing what a replay does. The seam is real — a queue/pool could be dropped in at the same `/api/run` boundary — but it isn't built (see §7).

**Three trade-offs.** Accessibility tree first (text survives branding/version drift; vision is a fallback; screenshots are failure evidence only). Artifact decoupled from transcript (the model's monologue is discarded; a typed description persists). Depth over breadth (see §7).

## 2. Artifact schema

The artifact is a **Capability** — a typed, versioned, reviewable description of one flow, stored as one diffable JSON file per task under `artifacts/` (version-controlled):

```
Capability
├─ meta (name, version, domain, start_url)
├─ inputs / outputs                          # typed specs; an input may carry a `bind` path
├─ checkpoint_text                           # how success is recognized
├─ business_outcomes / failure_patterns      # deterministic text signals
├─ examples                                  # runnable input→result cases, each with an optional `expect`
└─ steps: [{ action, target{strategy, role, name, ordinal}, value, assertion, on_error }]
```

It is shaped around six principles: **intent not coordinates** (locate by role+name, never pixels — one vendor's instance transfers to another's); **decoupled from transcript**; **every step asserts**; **errors are first-class** (`business_outcomes` = legitimate answers, `failure_patterns` = hard stops); **human-manageable** (Pydantic-validated, diffable JSON); **customer data encrypted at rest** (AES-256-GCM, per-tenant key).

A key structural decision: **input parameters are placeholders, not values.** A `type` step records `{member_id}`, not `"1001"` — the artifact captures *intent*, and the concrete value is supplied at replay time. That is what lets one artifact serve many users and many institutions.

**Per-user binding.** An input may declare a `bind` path (e.g. `userBO.eeID`). At replay the value is resolved from the *calling user's* private folder, never from the request body — so a member id bound to an employee is auto-filled and cannot be spoofed.

Every task also declares a **standardized output contract**: **success** → typed fields (`{"name": …, "status": …}`); **business_outcome** → `{"outcome": "<label>"}`; **failure** → no JSON. A caller branches on the shape without inspecting the page.

## 3. Determinism & error handling

Replay is **act → assert → branch**, classifying every result into one of three states: **success** (goal reached, typed outputs extracted), **business_outcome** (a legitimate expected answer — a page-returned error is a *correct* answer, not a crash), and **failure** (a mid-run error-out that produced no JSON). From the caller's view this collapses to a binary: **succeeded = success + business_outcome**; only `failure` is real.

**How determinism is achieved.** Locator strategy is an enum — `accessibility` (role + name, falling back to role + ordinal) is primary because it targets *semantic* structure rather than volatile coordinates; `text`/`css`/`xpath` are fallbacks for non-semantic surfaces, and `visual` (coordinate clicking) is reserved but unbuilt. Every step carries an assertion, so the engine never assumes a click worked. Async single-page renders are handled by a bounded checkpoint poll (a short deadline, not an open wait). Recoverable failures retry a bounded number of times; hard failures stop.

**`expect` tightens it further.** A run carrying an output assertion must *satisfy* it or it is reclassified **failure** — even if the checkpoint matched. A well-formed JSON with the wrong fields means the task itself is broken: the one failure a bare "did it return JSON?" check can't catch. Enforced server-side, so stats, task health, and replay all see it as a true failure.

**The observation loop.** Every run is recorded and re-runnable. Failures (the only no-JSON case) keep their input **encrypted** (`enc:…`, never decrypted in the report) plus a one-click **Replay** to reproduce the exact invocation; successful runs store no customer data at all. A failing task is fixed conversationally with **AI optimize** (plain-English → validated structured patch → version auto-bumped), run as a background job. Each task carries a three-state health signal — **verified / unverified / error** — anchored on the last optimize; a broken task is highlighted in admin and **hidden from users** until re-verified.

## 4. Heterogeneity & multi-tenant

The schema is surface-agnostic: `action` is a small typed vocabulary and `target.strategy` is the only place a surface leaks in, so a desktop driver or a legacy frameset app is a new *strategy*, not a rewrite. Recording intent + locator strategy (not coordinates) means one tenant's artifact applies to a differently-branded instance of the same app. Known boundary: canvas/image-only surfaces need coordinate clicking — `visual` is reserved but not built, since the targets are DOM-based.

**Multi-tenant reuse is the core of the product.** The artifact (the *how*) is shared; the *inputs* and *credentials* are per-user. Concretely: each user owns a private folder; the legacy credential is injected from it (decrypted only at the moment of use, under a per-user key `SHA-256(master ‖ user_id)`), and inputs bound to per-user data are auto-fetched. So one institution's artifact runs safely for many employees without any shared service account or hard-coded customer data.

## 5. Escalation & handoff

A real control-transfer state machine on the same live session: `AUTOMATION → PAUSED → HUMAN → (resume)`. The system detects a blocked or hard-failed state, hands the live browser session to a human operator, and resumes where it paused while recording the human's actions — so it can always answer "who is in control," and a human can steer an edge case without aborting the flow.

## 6. Safety

Three layers: an **allowlist** (domains + action types, enforced in discovery *and* replay, so the model can't wander off-site even during authoring); **action gating** (risky/irreversible actions blocked or escalated); and **data handling** (inputs AES-256-GCM encrypted at rest with per-tenant/per-user keys, decrypted only at the moment of use, never revealed in the report — successful runs store no customer data). The explicit threat model is **accidental mis-action, not a determined adversary**: the guardrails bound the blast radius of a wrong model decision; they are not a security boundary against an attacker who controls the host.

## 7. Cuts

Depth over breadth, applied to the load-bearing pieces (artifact schema, deterministic replay + error handling, safety/escalation). Deliberately cut, each with a real seam:

- **No queue/cluster/pooling** — synchronous single-process replay is simpler and sufficient to prove correctness; the `/api/run` boundary is where a worker pool would attach (§1).
- **No coordinate clicking / desktop driver** — `visual` strategy and the `desktop` surface are typed into the schema but unbuilt, since the target apps are DOM-based.
- **No commercial production target** — proven against a deliberately-hostile local mock (1998-era banking portal: table-based layouts, no test IDs, multi-modal outcomes per input) plus the public SauceDemo / The Internet sandboxes, never real credentials or PII.

With more time, next: capability dedup (one flow, many recordings), automatic error-state discovery (probe the failure paths during authoring), generalized output extraction, and the queue/browser-pool layer for multi-tenant scale.

---

See **[README.md](README.md)** for the full runbook — install, demo path, the mock's multi-modal inputs, the per-user login/credential model, input binding, and the statistics / AI-optimize loop in detail.
