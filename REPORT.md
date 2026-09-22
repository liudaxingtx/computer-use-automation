# REPORT — Computer-Use Automation System

> Design write-up for the interface.ai take-home. The implementation is complete and validated by repeatable runs under `/evidence/` and `/artifacts/`.

## 1. Architecture

The target backend exposes no API, so the only way in is to drive the UI like a human. The core decision is a **two-phase split — LLM discovery once, deterministic replay forever**. The LLM is used only during workflow authoring; runtime execution is model-free, which removes the non-determinism, cost, and latency of an in-loop LLM from everyday operation.

Three roles sit over one execution core:

**Admin control plane** — AI-assisted management: discover new flows, optimize and version artifacts, bind input schemas, and watch task health and failure telemetry. Broken tasks are hidden from users and surfaced in the repair inbox.

**User runner plane** — a login-gated surface that serves only non-broken (non-error) tasks. It enforces per-user isolation: each user has a private folder, their own legacy credential is injected at runtime (never a shared account), and bound user attributes are auto-resolved rather than typed or spoofable.

**Append-only audit plane** — records every invocation with the caller's identity. Hard failures are marked error, hidden from users, and persisted with AES-256-GCM-encrypted inputs; successful runs store no customer data at all.

**Key decisions & trade-offs**

- **Accessibility-tree-first driving** — Playwright's semantic role/name locators, not coordinates or screenshots. Trades universal visual coverage for long-term replay stability; a vision model is only the fallback for image-only surfaces.
- **Thin stdlib HTTP layer** (`ThreadingHTTPServer`) — no framework, so the codebase stays focused on the engine, not HTTP plumbing.
- **Synchronous single process, heavy work in background threads** — no queue/worker complexity; discovery and optimize run in background threads so they never block a live call. Trades horizontal scaling for simplicity and deterministic guarantees.

## 2. Artifact schema

All workflows persist as versioned, diffable, Pydantic-validated **Capability** artifacts — one JSON file per task under `/artifacts/`. The schema stores **intent, not runtime values or the agent transcript**, which is what enables reuse, human review, and deterministic replay.

```
Capability
├─ meta (name, version, domain, start_url)
├─ inputs / outputs                          # typed specs; an input may carry a `bind` path
├─ checkpoint_text                           # the success signal
├─ business_outcomes / failure_patterns      # deterministic text signals
├─ examples                                  # runnable input→result cases, each with an optional `expect`
└─ steps: [{ action, target{strategy, role, name, ordinal}, value, assertion, on_error }]
```

- **Inputs are placeholders, not values** — a `type` step records `{member_id}`, not `"1001"`. The artifact captures *how to run the flow*; credentials, user data, and environment specifics are injected at runtime. This is the seam for multi-tenant reuse.
- **Dual outcome classification** — `business_outcomes` (legitimate expected answers) are separated from `failure_patterns` (hard errors), so a page-returned error is a *correct* result, not a crash.
- **Per-step assertions and `on_error` rules** — the engine never assumes an action worked; every step verifies and declares its failure behavior.
- **Per-user binding** — an input may declare a `bind` path (e.g. `userBO.eeID`), resolved from the calling user's folder at replay, so bound identity fields can't be spoofed.
- **Versioned, diffable** — plain JSON under version control, AI-optimized patches auto-bump the version, and the artifact stays human-reviewable (unlike an opaque transcript).

## 3. Determinism & error handling

Replay is a strict **act → assert → branch** loop that classifies every run into three states: **success** (goal reached, typed outputs extracted), **business_outcome** (a valid non-success business state), and **failure** (an uncontrolled mid-run crash with no output). For a caller this collapses to binary: **succeeded = success + business_outcome**; only `failure` counts as broken.

- **Semantic locators first** — role+name (accessibility tree) is primary, text/CSS/XPath are fallbacks; coordinates are avoided. This removes flakiness and tolerates branding/version drift.
- **Every step asserts** — no silent partial failures; each click/input/navigation is verified before proceeding.
- **Bounded polling and retries** — async SPA renders are handled by a deadline-bounded checkpoint poll (not an infinite wait); recoverable errors retry a capped number of times.
- **`expect` output assertions** — beyond page-checkpoint matching, a structured output assertion hard-gates the result. A page that loads but returns wrong data is reclassified failure: the one failure mode a bare "did it return JSON?" check can't catch.

**Failure → repair loop.** Every run is recorded and re-runnable. Failures (the only no-output case) keep their input encrypted (`enc:…`, never decrypted in the report) with a one-click **Replay** of the exact invocation; successful runs store no customer data. A broken task is fixed conversationally with **AI optimize** (plain-English → validated structured patch → version auto-bump), run as a background job. Each task carries a **verified / unverified / error** health signal anchored on the last optimize; a broken task is highlighted in admin and hidden from users until re-verified.

## 4. Heterogeneity & multi-tenant

The schema is surface-agnostic: `action` is a small typed vocabulary and `target.strategy` is the only place a surface leaks in, so a legacy table-based portal, an outdated frameset, or a modern SPA is a new *strategy*, not a rewrite. A reserved `visual` (coordinate) strategy exists for future image/canvas surfaces but is unbuilt — the current targets are DOM-based.

**Multi-tenant reuse is the core product design.** The artifact (the *how*) is shared; the *credentials and inputs* (the *whose*) are per-user. Each user owns a private folder; credentials decrypt only at the moment of use under a per-user AES-256-GCM key (`SHA-256(master ‖ user_id)`); bound identity fields (employee id, member id) are auto-resolved from private state. No shared service accounts, no hard-coded customer data, no cross-user leakage. One verified artifact runs safely across many employees and many branded instances of the same app.

## 5. Escalation & handoff

The system has a real **control-transfer state machine** — `AUTOMATION → PAUSED → HUMAN → resume` — that tracks who is in control of a live session and records each transfer (pause / cede / resume) with its reason. On a hard failure the automation pauses itself, and the state machine answers "who is in control" at every point.

The **operator UI** — where a human actually drives the live session to resolve an edge case — is stubbed at a clean seam: the `cede`/`resume` transitions and the event history are real, but the interactive human-take-over loop is a documented cut (see §7), not a built UI.

## 6. Safety

Guardrails bound the blast radius of a wrong model decision or a bad artifact edit; the threat model is **accidental mis-action, not a determined adversary**.

- **Allowlist** (active) — approved domains and action types are enforced in both discovery and replay, so the model can't navigate off-domain or act outside the vocabulary even during authoring.
- **Data handling** (active) — sensitive inputs are AES-256-GCM encrypted at rest with per-tenant/per-user keys and decrypted only at the moment of use; successful runs persist no customer data.
- **Action gating** (seam, not active) — the policy schema has a `risky_actions` list for blocking/escalating irreversible actions, but it is currently empty and not wired; it's a defined extension point, not current behavior.

## 7. Cuts

Depth over breadth, applied to the load-bearing pieces (artifact schema, deterministic replay, safety, isolation, escalation). Deliberately cut, each with a real seam:

- **No queue/cluster/browser-pooling** — synchronous single-process replay proves correctness; the `/api/run` boundary is where a worker pool would attach.
- **No coordinate clicking / desktop driver** — the `visual` strategy and the `desktop` surface are typed into the schema but unbuilt.
- **No interactive operator UI** — the handoff state machine is real; the human-drives-the-live-session loop is stubbed (see §5).
- **No commercial production target** — proven against a deliberately-hostile local mock (1998-era banking portal: table layouts, no test IDs, multi-modal outcomes) plus the public SauceDemo / The Internet sandboxes; never real credentials or PII.

**Future work.** With more time, two things lead:

- **Closed-loop repair.** Today the fix loop is human-steered: a maintainer notices a failed task, replays the failing case, describes the fix to the AI optimizer, and re-runs until it passes. That loop can be automated end-to-end, because the system already holds the verification data it needs — the recorded failure, its exact inputs, and the `expect` assertion. On a hard failure it can auto-replay, ask the decision model for a patch, re-run, and verify, iterating until the case passes or control is handed to a human. The primitives exist (Replay, AI optimize, version bump, `expect`); only the orchestration loop is new.
- **Per-task regression tests.** Runnable examples already carry `expect` assertions — they are test cases in disguise. The missing piece is a runner that executes every example as a test and reports pass/fail per task, so a fix is proven against all known cases rather than only the one that failed. Newly-discovered failing cases should be auto-captured into that set: a failing invocation becomes a regression test.

Also on the list: capability dedup, automatic error-state discovery, generalized output extraction, and the queue/browser-pool layer for multi-tenant scale.

---

See **[README.md](README.md)** for the full runbook — install, demo path, the mock's multi-modal inputs, the per-user login/credential model, input binding, and the statistics / AI-optimize loop.
