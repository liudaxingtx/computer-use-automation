# Computer-Use Automation System

> **interface.ai — Engineering take-home project**

An LLM-driven system that gives AI agents hands on legacy software that has **no API**: it *discovers* how to operate a UI, *records* the successful run as a structured artifact, and *replays* it deterministically — **no LLM in the loop** — so an agent can invoke it reliably and cheaply in production.

---

## The problem → the solution

interface.ai's agents must operate back-office systems (bank, insurance, healthcare) whose only interface is a web UI. That means driving the browser like a human. The naive way — ask a model at every click — is slow, expensive, and non-deterministic.

This system inverts that:

```
        discovery (LLM, once)                 replay (deterministic, forever)
  ┌─────────────────────────────┐         ┌─────────────────────────────────┐
  │ observe → decide → act      │  distill │ act → assert → branch          │
  │ (a live browser session)    │ ───────► │ (a typed Capability artifact)  │
  └─────────────────────────────┘          └─────────────────────────────────┘
          "how do I do this?"                  "do this, with these inputs"
```

**The model discovers. The artifact replays.** The LLM appears exactly once — during discovery. What it learned becomes a *Capability*: a versioned, reviewable record of *what* to do, *how to locate* each control, and *what to expect* after each step. Production invocations replay that artifact with plain deterministic code — no model, no reasoning, no cost.

---

## Quick start

```bash
# 1. Install (Python 3.12)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

# 2. Configure API keys (optional — only needed to *record new* tasks)
cp .env.example .env      # fill in DEEPSEEK_API_KEY and KIMI_API_KEY

# 3. Start the local target app (a "legacy bank" mock, port 9000)
python3 mock-app/server.py

# 4. Start the dashboard (port 8123)
.venv/bin/python -m dashboard.server

# 5. Open http://localhost:8123
```

You'll see a dashboard of pre-recorded tasks grouped by target site, each invokable with your own inputs. **Viewing and replaying the pre-recorded tasks needs no API keys** — replay is deterministic, no LLM in the loop. Keys are only required to record *new* tasks. Full walkthrough: **[`GETTING_STARTED.md`](GETTING_STARTED.md)**.

---

## The demo — seven recorded tasks

The engine is proven against **two kinds of target**: a deliberately-hostile local mock *and* two live, publicly-deployed test sites.

| Site | Task | What it does |
|---|---|---|
| **Local mock** (`localhost:9000`) | `lookup_member` | search a member by ID and read their detail |
| | `deactivate_member` | search → view detail → deactivate an account |
| | `register_operator` | fill and submit the registration form |
| | `login_operator` | fill and submit the login form |
| **SauceDemo** (real site) | `saucedemo_login` | log into the Swag Labs store |
| | `saucedemo_checkout` | **login → add-to-cart → cart → checkout → finish an order** (11 steps) |
| **The Internet** (real site) | `theinternet_login` | log into the Secure Area |

`saucedemo_checkout` is the headline: a full multi-step transactional flow discovered and replayed against a **live React storefront** — which forced two real-world fixes that a mock never would have (see below).

---

## How it works

1. **Discover** — the decision LLM is shown a live browser (URL + accessibility tree + a numbered menu of controls) and decides one action at a time. Image-only surfaces fall back to a vision model.
2. **Record** — the successful transcript is distilled into a `Capability`: typed steps, locator *strategies* (role+name, never pixels), per-step assertions, and first-class error states.
3. **Replay** — a deterministic engine re-runs the artifact: it re-resolves each locator against the live page, acts, then classifies the result into one of **three states**:
   - **success** — goal reached, structured outputs extracted from the page;
   - **business_outcome** — a legitimate answer ("no such member"), not a crash;
   - **failure** — a hard stop with a debuggable diagnostic.

The three-state taxonomy is the heart of the design: "no such member" is a *valid answer*, not an error. It's proven against a mock that plants exactly these traps.

---

## Repository layout

```
agent/        discovery loop · artifact model · deterministic replay · safety · crypto · observability
dashboard/    the task-management console (served at http://localhost:8123)
mock-app/     a deliberately-hostile local stand-in for a legacy bank back-office app
evidence/     artifacts + logs from real discovery and replay runs (append-only)
scripts/      one-command tests, screenshot/evidence generators, artifact helpers
```

---

## Features

- **Discovery loop** — accessibility-tree observation + vision fallback + structured LLM decisions.
- **Typed artifact** — a versioned, human-reviewable `Capability` (diffable JSON, not a model monologue).
- **Deterministic replay** — act → assert → branch, with bounded retries and hard-stop escalation.
- **Structured data extraction** — on success, the result page is read back into JSON (normalized field matching).
- **One-click recording** — give a URL + a plain-English sentence, get a recorded + verified task back (`POST /api/discover`).
- **Observability** — append-only run store, success telemetry, a failure inbox, and a replay-error → repair → re-verify loop.
- **Safety** — action allowlist (enforced in discovery *and* replay), AES-256-GCM encryption of customer inputs at rest, and a human handoff state machine.
- **Task dashboard** — grouped by domain, per-task locator detail + screenshots, Swagger-style invoke, call log, delete (with confirm).

---

## Testing

```bash
./scripts/run_tests.sh
```

Boots the mock and runs the real end-to-end suite: discovery → artifact → replay across all three states → safety/encryption/handoff → vision fallback → observability. It makes **real LLM calls** — that's the point: a genuine run, not a stubbed result.

---

## Design highlights

The reasoning behind every decision is in [`DESIGN.md`](DESIGN.md). A few worth calling out:

- **Intent, not coordinates** — artifacts record *how to locate* a control (role + name), so one recorded on institution A applies to differently-branded institution B.
- **Every step asserts** — a click's success is never assumed.
- **Repeated-control disambiguation** — a real storefront has six identical "Add to cart" buttons; locators record their position *within* the name group so replay targets the right one.
- **Async-render (SPA) waiting** — real sites render results after navigation; replay polls for the success signal before judging failure.
- **Reasoning-model output drift** — the decision model's reasoning monologue can consume the output budget; a generous budget + `reasoning_content` fallback + retry makes drift self-heal.

---

## Documentation

| Doc | For |
|---|---|
| **[`GETTING_STARTED.md`](GETTING_STARTED.md)** | Run it, configure it, verify it, see what was built and what was deliberately cut |
| [`REPORT.md`](REPORT.md) | The formal submission write-up (the seven mandated headings) |
| [`DESIGN.md`](DESIGN.md) | The full working design record — the reasoning behind every decision |
| [`TODO.md`](TODO.md) | Progress tracker |
