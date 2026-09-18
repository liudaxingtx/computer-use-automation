# TODO & Progress Tracker

Legend: ✅ done · 🚧 in progress · ⬜ not started

## ✅ Done — what's implemented & when

| Date | Item |
|------|------|
| 2026-09-17 | Phase 0 — design & strategy (`DESIGN.md`, `README.md`, `REPORT.md` skeleton) |
| 2026-09-17 | Chinese mirror docs (`DESIGN.zh.md`, `README.zh.md`, `REPORT.zh.md`) |
| 2026-09-17 | Repo initialized + git identity (Da Liu / liudaxingtx) |
| 2026-09-17 | K3 API key verified — endpoint `api.moonshot.cn`, model `kimi-k3`, vision reads an image, reasoning model |
| 2026-09-17 | Phase 1 — mock app (`mock-app/server.py`): "Legacy Member Services" with 3 runtime states planted |
| 2026-09-17 | Phase 2 — agent loop (`agent/`): a11y-tree observation + DeepSeek structured decisions + K3 vision fallback; real end-to-end run (search 1001 → deactivate) in 5 steps |

## 🎯 Roadmap

### Phase 1 — Mock app (legacy bank back-office stand-in)
- ✅ build the hostile mock: table layouts, no test IDs, deeply nested markup
- ✅ plant three runtime states — "no such member" (business outcome), a confirm dialog (recoverable), a permission-denied state (hard failure)
- ✅ wire one non-trivial multi-step flow (search → detail → action)

### Phase 2 — Agent loop (discovery)
- ✅ Playwright + accessibility-tree observation (primary)
- ✅ Kimi K3 vision fallback for non-semantic surfaces (image-only gate: understands + returns coordinates)
- ✅ DeepSeek structured decisions (observe → decide → act)
- ✅ one real end-to-end discovery run against the mock

### Phase 3 — Artifact
- ⬜ Pydantic artifact schema (Capability)
- ⬜ serialize a discovery run → Capability
- ⬜ versioning + reviewability

### Phase 4 — Deterministic replay
- ⬜ replay engine: act → assert → branch
- ⬜ three-state result contract (success / business_outcome / failure)
- ⬜ recoverable vs hard-failure handling (bounded retries, hard stops)

### Phase 5 — Safety & escalation
- ⬜ configurable allowlist (domains/routes + action types), enforced in loop and replay
- ⬜ pause / cede / resume handoff state machine on the live session + mocked operator UI

### Phase 6 — Evidence
- ⬜ `/evidence/`: saved artifact + discovery log + replay log
- ⬜ one replay that hits an error / exceptional state

### Phase 7 — REPORT.md
- ⬜ distill `DESIGN.md` into the seven mandated headings

## 🔜 Next up

**Phase 3 — Artifact** (the load-bearing schema).

1. Pydantic `Capability` schema — meta, inputs, outputs, checkpoint, typed steps
2. serialize a discovery run → Capability (distill steps + locator strategies from the transcript)
3. versioning + reviewability (human-readable, diffable)

## 📦 Before final submission

- [ ] re-hide `*.zh.md` in `.gitignore` and remove them from the repo
- [ ] confirm no secrets (`.env`) are committed
- [ ] push to a public GitHub repo (liudaxingtx)
- [ ] email the repo link to assignments@interface.ai
