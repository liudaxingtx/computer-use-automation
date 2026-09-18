# Computer-Use Automation System

> **interface.ai — Engineering take-home project**

A backend integration layer that gives AI agents *hands*: an LLM-driven system that discovers how to operate a legacy (no-API) application, records the successful run as a structured artifact, and replays it deterministically — no LLM in the decision loop — so the agent can invoke it reliably and cheaply in production.

---

## What this repo is

| Path | Purpose |
|------|---------|
| `DESIGN.md` | **Our working design record** — how we understand the problem, the reasoning behind every decision, and our tracked implementation plan. The single source of truth we work from. |
| `REPORT.md` | Final submission write-up (the 7 mandated headings). Distilled from `DESIGN.md` at the end. |
| `src/` | The implementation (agent loop, artifact model, replay engine, safety, escalation, evidence). |
| `evidence/` | Artifacts + logs from a real discovery run and a real replay run. |
| `mock-app/` | A deliberately hostile local stand-in for a legacy bank back-office system. |

## Status

**Phase 0 — design & strategy** (current). See `DESIGN.md` §10 for the roadmap.

---

## The core idea in one line

> **The model discovers. The artifact becomes a reusable capability. Deterministic replay is how the agent invokes it in production.**

The LLM appears exactly once — during discovery. After that it leaves the loop. What it learned becomes a *capability* the agent calls without re-reasoning about the UI.

Full reasoning: see `DESIGN.md`.
