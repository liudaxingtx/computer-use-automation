# Computer-Use Automation System

> **interface.ai — Engineering take-home project**

An LLM-driven system that gives AI agents hands on legacy software that has **no API**: it *discovers* how to operate a UI, *records* the successful run as a structured artifact, and *replays* it deterministically — **no LLM in the loop** — so an agent can invoke it reliably and cheaply in production.

> **The design write-up is [`REPORT.md`](REPORT.md)** — architecture, artifact schema, determinism & error handling, heterogeneity & multi-tenant, escalation & handoff, safety, and cuts. This README is the runbook.

---

## 1. Installation & Usage

### 1.1 Requirements

- Python 3.12
- Network access to the target sites (for the two live demo sites)

### 1.2 Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### 1.3 Configure

```bash
cp .env.example .env      # fill in the two API keys (optional — see below)
```

There are two LLM roles, both **provider-agnostic** — point each at any OpenAI-compatible endpoint (DeepSeek, Kimi/Moonshot, OpenAI, OpenRouter, a local vLLM, …). See `.env.example` for a multi-provider template.

> **You only need keys to *record new* tasks.** Viewing and replaying the pre-recorded tasks needs **no keys at all** — replay is deterministic, no LLM in the loop.

### 1.4 Run

```bash
# start the local target app (a "legacy bank" mock, port 9000)
python3 mock-app/server.py

# start the dashboard (port 8123) — serves BOTH interfaces
.venv/bin/python -m dashboard.server
```

Then open:

| URL | Who it's for | What it does |
|---|---|---|
| `http://localhost:8123/` | **Admin console** | Manage tasks, view locator detail + screenshots, run/verify, statistics report (per-task user-call rates + replay failed calls in a full result modal), AI-optimize any task conversationally, delete, register new tasks |
| `http://localhost:8123/user` | **User runner** | Pick a task from a dropdown → fill its inputs → run → see the result |

### 1.5 Demo path — discover a goal, then replay it

The end-to-end thread, in two commands (with the mock running):

```bash
# 1. Discover — the LLM drives the mock to accomplish a goal, records a Capability
.venv/bin/python -m agent.main --task "Look up member 1001 and read their balance"

# 2. Replay — deterministic, no LLM in the loop
.venv/bin/python -m agent.cli verify lookup_member --input member_id=1001
```

Step 1 is the *only* step that needs API keys and makes real LLM calls; step 2 is pure deterministic code. Evidence for both lands under `evidence/`.

### 1.6 Test suite

```bash
./scripts/run_tests.sh
```

Boots the mock and runs the real end-to-end suite (discovery → artifact → replay across all three states → safety/encryption/handoff → vision fallback → observability). It makes **real LLM calls** — a genuine run, not a stub.

---

## 2. Project Structure

Three blocks over one engine:

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

Both interfaces are thin HTTP frontends over the **same** engine and the same `POST /api/run` replay endpoint; they differ only in what they expose.

```
agent/        the engine — discovery loop, artifact model, deterministic replay,
              safety, encryption, observability
dashboard/    both web interfaces — server.py, index.html (admin), user.html (runner),
              screenshots/
mock-app/     a deliberately-hostile local stand-in for a legacy bank back-office app
artifacts/    the recorded solution paths — one Capability JSON per task (version-controlled)
evidence/     an example artifact + raw discovery transcripts + append-only run logs
              (inputs encrypted at rest)
scripts/      one-command tests, screenshot/evidence generators, artifact helpers
```

---

## 3. Design

The full design argument — architecture, artifact schema, determinism & error handling, heterogeneity & multi-tenant, escalation & handoff, safety, and cuts — is in **[`REPORT.md`](REPORT.md)**. It also documents the demo target (the "legacy bank" mock and its multi-modal inputs), the statistics report, the AI-optimize conversational repair loop, and the implemented stretch goal (agent-facing capability interface).
