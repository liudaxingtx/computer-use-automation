# Computer-Use Automation System

> **interface.ai — Engineering take-home project**

An LLM-driven system that discovers how to operate a legacy (no-API) application, records the successful run as a structured artifact, and replays it deterministically — no LLM in the loop — so an agent can invoke it reliably and cheaply in production.

---

## Where to start

| Doc | For |
|---|---|
| **`GETTING_STARTED.md`** | **Start here** — run it, configure it, verify it, and see what was built and what was deliberately cut |
| `REPORT.md` | The formal submission write-up (the seven mandated headings) |
| `DESIGN.md` | The full working design record — the reasoning behind every decision |
| `TODO.md` | Progress tracker |

## The repo

| Path | Purpose |
|------|---------|
| `agent/` | The implementation: discovery loop, artifact model, replay engine, safety, escalation, crypto, observability |
| `dashboard/` | The task-management dashboard (served at `http://localhost:8123`) |
| `mock-app/` | A deliberately hostile local stand-in for a legacy bank back-office app |
| `evidence/` | Artifacts + logs from real discovery and replay runs (append-only) |
| `scripts/` | One-command tests, evidence/screenshot generators, artifact helpers |

## The core idea in one line

> **The model discovers. The artifact becomes a reusable capability. Deterministic replay is how the agent invokes it in production.**

The LLM appears exactly once — during discovery. After that it leaves the loop. What it learned becomes a *capability* the agent calls without re-reasoning about the UI.

Full reasoning: `DESIGN.md`. To run it yourself: `GETTING_STARTED.md`.
