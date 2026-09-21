# REPORT — Computer-Use Automation System

> Final submission write-up. The implementation is complete and backed by real runs under `/evidence/`.

## 1. Architecture

interface.ai's agents must drive back-office software that has **no API** — the only way in is to operate the UI like a human. This project is the backend integration layer that gives those agents hands: **an LLM discovers how to accomplish a task once, the successful run is distilled into a typed, reusable artifact, and a deterministic engine replays it with no LLM in the loop.**

The whole design hangs on one invariant: **the LLM appears exactly once, during discovery, then leaves the loop.** Everything else protects that separation — it is what makes this a real design rather than "a Playwright wrapper."

The system is a single process with clean module boundaries:

```
agent loop  →  artifact model  →  replay engine
                 ↑                    ↑
              safety/guardrails    escalation/handoff
                 └─────── evidence/observability ───────┘
```

Observation uses three eyes, each with one job: the **accessibility tree** (primary — text, cheap, and more stable than raw DOM on legacy apps), a **vision model** (Kimi K3, fallback for non-semantic surfaces), and **screenshots** (evidence on failure). Decisions are made by DeepSeek with structured JSON output.

## 2. Artifact schema

The artifact is a **Capability**: a typed, versioned, reviewable description of one reusable flow. It records *what* to do (steps), *how to point at* each control (a locator strategy, never pixels), *what to expect* after each step (an assertion), and *how to classify* failures (error handling). It is deliberately decoupled from the raw LLM transcript.

```
Capability
├─ meta: name, version, description, surface, created_at
├─ inputs / outputs                     # typed specs, e.g. member_id: str
├─ checkpoint + checkpoint_text         # how we know the goal was reached
├─ business_outcomes / failure_patterns # deterministic text signals for classification
└─ steps: [{ action, target{strategy, role, name, ordinal, reasoning},
             value, assertion, on_error }]
```

Six principles govern it: (1) **intent, not coordinates** — we record *how to locate*, never pixels; (2) **decoupled from the transcript** — distilled, typed steps survive even if the model changed its mind mid-run; (3) **every step carries an assertion** — we never assume a click worked; (4) **errors are first-class** — each step declares its failure modes; (5) **human-manageable** — plain diffable JSON, review/edit/version like code; (6) **customer data encrypted at rest** — input values are AES-256-GCM encrypted with a per-tenant key, decrypted only at replay time, while the locator strategy stays plaintext so the artifact remains reviewable.

## 3. Determinism, locator & robustness strategy

Replay is **act → assert → branch**, never blind re-execution. It re-resolves each locator against the live page and classifies the result by deterministic text signals — never by asking a model to look again.

The result contract has exactly three states: **success** (goal reached, typed outputs), **business_outcome** (a legitimate expected answer like "no such member" — *not* a crash), and **failure** (a hard stop with a debuggable diagnostic: which step, expected vs observed). Recovery is separated from failure: recoverable conditions (dismiss an interstitial, retry a transient load) are handled inline with bounded retries; hard failures stop and route to escalation. This three-state contract is **proven, not asserted** — `/evidence/` carries real replay runs for all three states against a deliberately hostile mock.

Locator strategy: **accessibility (role+name) is primary**, with an ordinal fallback for empty-name controls, plus text/css/xpath. One deliberate boundary: **we do not ship coordinate/visual clicking.** The schema reserves a `visual` strategy, and the K3 vision fallback already *locates* non-semantic controls by returning coordinates — but the act vocabulary has no coordinate action, so fully visual surfaces (canvas, image captchas) are *understood* but not yet *executable*. We document this as a known boundary with a known fix (a `visual` locator of template-match or normalized coordinates) rather than implement it: it conflicts with "intent, not coordinates," and only matters for the rarest surfaces.

## 4. Heterogeneity & multi-tenant

The artifact schema is **surface-agnostic**: `action` is a small typed vocabulary (click/type/select/navigate/wait), and `target.strategy` is the only place a concrete surface leaks in — a desktop surface is a new strategy plus a driver behind the same engine, not a rewrite. Because we record *intent + locator strategy* (not coordinates), an artifact recorded on one institution's instance of a vendor product applies to a second, differently-branded instance, with per-variant overrides where a route or label differs. This falls out of the schema for free.

## 5. Escalation & handoff

A real control-transfer state machine on the **same live session**:

```
AUTOMATION ──stuck/risky/irreversible──▶ PAUSED ──operator takes over──▶ HUMAN
    ▲                                                                     │
    └──────────────── resume after operator signals ──────────────────────┘
```

The system detects a blocked state, raises an intervention request (capability, step, screenshot, why it stopped), lets the operator drive the *live* session, then resumes from where it paused while recording the human's actions. The system can always answer "who is in control." The operator console is mocked, but the pause/cede/resume mechanism and control-ownership model are real — a hard failure demonstrably pauses to a human and resumes afterward.

## 6. Safety

Three layers. **Allowlist:** permitted domains/routes and action types, enforced in the agent loop *and* re-checked in replay — the agent cannot act outside it. **Action gating:** risky/irreversible actions (submit, delete, approve) are classified and blocked or routed to escalation. **Data handling:** customer-entered values are AES-256-GCM encrypted with a per-tenant key before writing to an artifact or ReplayRun, decrypted only at use; extracted outputs are returned to the caller and never persisted. Keys come from the environment, never from git.

## 7. Cuts

Depth over breadth, deliberately: **no queues, clusters, or multi-tenant plumbing** (the brief discourages premature infrastructure); **no automation against public sites** (ToS and uncontrollable state — we prove the error taxonomy on a local hostile mock instead); **no coordinate/visual clicking** (see §3 — a documented boundary with a known fix, not a shipped capability). Each cut trades feature breadth for real depth on the three load-bearing pieces — artifact schema, deterministic replay with an error taxonomy, and human handoff — plus a working observability/repair loop.

---

## Appendix — How to run & verify

Three ways to verify the work, fastest to deepest:

**1. One-command test suite.** `./scripts/run_tests.sh` boots the mock, then runs the real end-to-end tests: discovery (DeepSeek decisions) → artifact serialization → deterministic replay across all three result states → safety/encryption/handoff → K3 vision fallback → observability. It makes real LLM calls, so it costs a few tokens — that is the point: a real run, not a mock.

**2. Inspect the evidence.** `evidence/` holds the artifacts of a real run: the distilled Capability (`artifact_deactivate_member.json`), the raw discovery transcript, three replay runs (`success`, `business_outcome` "no such member", `failure` "access denied"), and the failure screenshot. Read these to see the three-state contract and encryption-at-rest working on real data.

**3. Drive the loop yourself.** With the mock running (`python3 mock-app/server.py`):

- Discover: `.venv/bin/python -m agent.main --task "Search for member 1001, view their detail, then deactivate the account."`
- Manage artifacts: `.venv/bin/python -m agent.cli list | telemetry | failures | replay-case <id> | resolve <id>`
- Reproduce the repair loop: `verify` → `edit --step 2 --new-name WRONG` (fails) → `edit --step 2 --new-name SEARCH` → `bump` → `verify` (passes)

The README carries the full runbook.
