# REPORT — Computer-Use Automation System

> Final submission write-up for the interface.ai take-home. This is the primary design document — the implementation is complete and backed by real runs under `/evidence/` and `/artifacts/`.

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

The data flow is **discover → record → replay**:

1. **Discover** — the decision LLM is shown a live browser (URL + accessibility tree + a numbered control menu) and decides one action at a time. Image-only surfaces fall back to a vision model.
2. **Record** — the successful transcript is distilled into a `Capability`: typed steps, locator *strategies* (role+name, never pixels), per-step assertions, first-class error states.
3. **Replay** — a deterministic engine re-runs the artifact: it re-resolves each locator against the live page, acts, then classifies the result into one of **three states**.

Key decisions and trade-offs:

- **Observation uses three eyes, each with one job.** The **accessibility tree** is primary (text, cheap, and more stable than raw DOM on legacy apps); a **vision model** is the fallback for non-semantic surfaces; **screenshots** are captured only as evidence on failure. Trade-off: we bias toward the accessibility tree even though screenshot+coordinates would look more "general", because the tree survives branding/version drift that raw pixels don't.
- **The artifact is decoupled from the transcript.** The raw LLM monologue is thrown away; what persists is a distilled, typed description a human can review and diff.
- **Single process, sync driver.** Simplicity over service sprawl — the brief explicitly discourages premature infrastructure (§7). The seams (a `target.strategy` enum, a driver behind `replay`) are where a future service/queue boundary would go.
- **Single process, but a concurrent control plane.** The engine itself stays a single sync process, but the dashboard that fronts it is a `ThreadingHTTPServer`: background jobs (AI optimize) run in worker threads behind a lock-guarded registry, so a long-running optimize never blocks live `/user` calls. Concurrency is in-process and bounded — still no queues or external services.

## 2. Artifact schema

The artifact is a **Capability**: a typed, versioned, reviewable description of one reusable flow. It is the focal point of the design, so it is shaped deliberately.

```
Capability
├─ meta: name, version, description, domain, start_url
├─ inputs / outputs                     # typed specs, e.g. member_id: str
├─ checkpoint + checkpoint_text         # how we know the goal was reached
├─ business_outcomes / failure_patterns # deterministic text signals for classification
├─ examples                             # runnable input→result examples, so a tester
│                                       #   can follow the list to verify each outcome
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

Each capability also carries **runnable `examples`** — concrete input values mapped to their expected result (success / business_outcome / failure) with a note on what the tester should see. The admin console renders these as a "Test examples" table, so a tester can replay every listed case and confirm the result matches without knowing the target system's internals. Every example is a real, verified input (the mock's planted member IDs, a fresh vs. taken username, a too-short password, the public SauceDemo / The Internet test credentials) — not a placeholder.

## 3. Determinism & error handling

Replay is **act → assert → branch**, never blind re-execution. It re-resolves each locator against the live page, acts, then classifies the result by deterministic text signals — never by asking a model to look again.

The result contract has exactly three states: **success** (goal reached, typed outputs extracted), **business_outcome** (a legitimate expected answer like "no such member" — *not* a crash), and **failure** (a hard stop with a debuggable diagnostic: which step, expected vs observed). This is the load-bearing distinction the brief calls out, and it is proven, not asserted — `/evidence/runs/` carries real runs for all three states.

The boundary is drawn deliberately: **a page-returned "error message" is a business_outcome, not a failure.** `ACCESS DENIED`, `INVALID PASSWORD`, "wrong password" — these are the target app *correctly reporting a business rule*, and the task did its job (it reached the page and read the answer back). Only a **mid-run error-out** — a locator that can't be resolved, a step that throws, a success checkpoint that never appears — is a `failure`, because that's the one case where no JSON answer was produced.

From the **caller's** point of view the contract collapses to a binary: **a call succeeded if it returned a usable JSON result.** Both `success` and `business_outcome` return structured JSON — success → the extracted fields, business_outcome → an explicit `{"outcome": "member not found"}` object — so both count as a *successful call*. Only `failure` — a mid-run error-out that returns no JSON — is a real failure. Telemetry mirrors this: `succeeded = success + business_outcome`, and the headline rate is `succeeded / total`, not `success / total`.

Robustness comes from four concrete mechanisms:

- **Locator strategy is an enum.** Accessibility (role+name) is primary; `name_ordinal` disambiguates repeated same-name controls (a real storefront has six identical "Add to cart" buttons); `ordinal` falls back for empty-name controls; text/css/xpath are the last resort. The strategy is chosen *at record time* with reasoning captured on the target.
- **Recoverable vs. hard failure are separated.** Recoverable conditions (a transient load, a known interstitial) retry with a bounded count via `on_error.then = retry`; hard failures stop and surface a diagnostic.
- **Async-render (SPA) waiting.** Real sites render results *after* navigation settles. Replay polls for the checkpoint (or an outcome) for a bounded window before declaring failure — this is the difference the mock alone couldn't expose, and it is what made the SauceDemo checkout replay reliable.
- **Reasoning-model output drift.** Reasoning LLMs can spend their whole budget on a monologue and return empty `content`; a generous budget, a `reasoning_content` fallback, and a retry make the discovery loop self-heal.

### Observability & the statistics report

Every invocation is **recorded, measured, and re-runnable**:

- **Append-only run store.** Each replay is one JSON file under `evidence/runs/`, keyed by task + timestamp + result — nothing is ever overwritten.
- **Real-time rates.** Telemetry aggregates, per task + version: `success`, `business_outcome`, `failure` counts, plus a headline **`succeeded`** (= success + business_outcome — every call that returned JSON) and its rate. `failure_rate` counts only hard error-outs, which is what an operator actually watches.
- **Failure inbox.** Every non-success run lands in an unresolved-failures inbox; a maintainer marks them resolved after a fix.
- **Replay-the-error loop.** Any failure can be re-run deterministically with its exact original inputs. The admin console's **↻ Replay** opens a full result modal — result pill, diagnostic, extracted JSON, a per-step execution log (green/amber/red, so the exact failing step is visible), and the failure screenshot — with an inline **AI optimize** box, so the maintainer can describe the fix against *that specific case* without leaving the replay view.
- **Statistics report (admin console).** A live report that aggregates, per task, the counts **and rates** (with a rate bar), then lists every *user* invocation (admin Execute/Replay ops are excluded). The headline is **succeeded vs failed**: succeeded calls (success *and* business_outcome — both returned JSON) show only timestamp + duration (their data isn't stored); failures — the only true errors, where the run errored out before returning JSON — show the **encrypted** input (`enc:…`, never decrypted in the report) + diagnostic and a **↻ Replay** button to re-run the exact failing invocation. A **↺ Reset runs** button clears every recorded call so a maintainer can start a fresh test run.

### Continuous optimization — a conversational repair loop

Artifacts are not frozen after recording: a maintainer tunes them **in plain English, not by hand-editing JSON.** Every task in the admin console exposes an **AI optimize** box (two entry points: the task detail, and the replay-result modal for fixing a specific failing case) — describe the fix ("the `status` output is always null — read the `h2` heading and `#flash` message instead") and the decision LLM returns a **structured patch** (outputs / checkpoint / business_outcomes / failure_patterns), pydantic-validated and persisted. List fields are **full replacement**: the LLM returns the complete final list, so an item can be added or removed in one step (the merge logic initially only *upserted*, which silently made "remove this output" a no-op — caught and fixed during testing).

Optimize is a **long-hold background job**:

- **Runs to completion server-side.** It executes in a background worker thread decoupled from the HTTP request — a maintainer can close the tab or navigate away and the LLM keeps working.
- **Never blocks live traffic.** Because the dashboard is a `ThreadingHTTPServer`, a slow optimize cannot stall concurrent `/user` calls; the job registry is lock-guarded.
- **Completion is surfaced to the page.** The console polls the registry and, when a job lands, shows a toast and refreshes the task view if the maintainer is still looking at it — no manual refresh.
- **It already paid for itself.** This is exactly how the `deactivate_member` hallucinated `status` output (a discovery-time LLM invention that always extracted `null`) was removed — one sentence, one validated patch, verified empty outputs afterward.
- **And it exposed a taxonomy bug.** `deactivate_member`'s "permission denied" failures were a *classification* error, not a system error: `ACCESS DENIED` (a restricted member can't be deactivated) had been recorded as a `failure_pattern` instead of a `business_outcome`, so a correctly-behaving task looked broken. Moving it (and `register_operator`'s `INVALID PASSWORD` / `INVALID INPUT`) to `business_outcomes` drove the recorded failure rate to zero.

## 4. Heterogeneity & multi-tenant

The schema is **surface-agnostic**: `action` is a small typed vocabulary (`click` / `type` / `select` / `navigate` / `wait`), and `target.strategy` is the only place a concrete surface leaks in. A desktop surface is a new strategy plus a driver behind the same engine — not a rewrite. A legacy web app (framesets, nested tables, no test IDs) is the same engine pointed at a messier tree.

Because we record **intent + locator strategy** (not coordinates), an artifact recorded on one tenant's instance of a vendor product applies to a second, differently-branded instance, with per-variant overrides where a route or label differs. This falls out of the schema for free. Per-tenant/version drift is the one place that genuinely needs the human repair loop: telemetry surfaces which (capability, version) pairs are failing, and the `edit`/`bump`/`verify` CLI is the fix path.

**Known boundary with a known fix — relative/coordinate clicking (the big one).** Some legacy surfaces are canvas- or image-only: no DOM, no accessibility tree, nothing to locate by role+name. Today the vision fallback *describes* such screens (including a recommended click point), but there is no "click at coordinates" action, so we can't actually drive them. The concrete plan:

1. The `visual` locator strategy is **already reserved** in the schema — extend the action vocabulary with a coordinate click (`click_at`).
2. **Record relative, not absolute.** During discovery, store the click point as an offset from a detected *landmark* (e.g. "the CONTINUE button center, relative to the page's bounding box") — never raw pixels, which break on any viewport/resolution change.
3. **Re-locate at replay time.** Re-run the vision model on the current screenshot to re-find the landmark and re-compute the click point, then `page.mouse.click(x, y)` — robust to resize, scroll, and different screens.
4. **Guard it.** Coordinate clicking is inherently less reliable than semantic locators, so it's an explicit, flagged per-step fallback that re-asserts after the click and escalates to a human on mismatch.

The honest tradeoff: this re-introduces a vision model into the replay path for *those specific steps* (bending the "no LLM in replay" invariant), which is why it's opt-in and kept out of the common path — and why we reserved it but haven't built it yet: the target back-office apps are DOM-based, so it's a rare fallback whose full cost (relative anchoring + re-location) didn't pay off for the mock/sauce-demo targets.

## 5. Escalation & handoff

A real control-transfer state machine on the **same live session**:

```
AUTOMATION ──stuck/risky/irreversible──▶ PAUSED ──operator takes over──▶ HUMAN
    ▲                                                                     │
    └──────────────── resume after operator signals ──────────────────────┘
```

The system detects a blocked state, raises an intervention request (capability, step, screenshot, why it stopped), lets the operator drive the *live* session, then resumes from where it paused while recording the human's actions. The system can always answer "who is in control." The operator console is mocked (a clean seam), but the pause/cede/resume mechanism and control-ownership model are real — a hard failure demonstrably pauses to a human and resumes afterward.

## 6. Safety

Three layers.

**Allowlist:** permitted domains/routes and action types, enforced in the agent loop *and* re-checked in replay — the agent cannot act outside it.

**Action gating:** risky/irreversible actions (submit, delete, approve) are classified and blocked or routed to escalation.

**Data handling:** customer-entered values are treated as secrets end to end —

- **AES-256-GCM encryption at rest.** Every input value is encrypted *before* it touches disk; the run log and artifacts never store plaintext customer data.
- **Decrypt only at the moment of use.** Replay decrypts an input solely to type it into the target form, then it is gone.
- **Per-tenant keys.** The master key derives a separate key per tenant (`SHA-256(master ‖ tenant)`), so one tenant's data is unreadable with another's key.
- **Authenticated encryption.** Wrong key or tampering raises — ciphertext can't silently decrypt to garbage.
- **Key never committed.** The master key lives in the environment (or a KMS in production), never in the repo.
- **Structure vs. data.** The locator strategy (UI structure) stays plaintext so artifacts remain reviewable; only the customer *data* is ciphertext.
- **The report never reveals decrypted failure inputs.** A failed run appears in the admin report as ciphertext (`enc:…`); replaying it decrypts server-side only at the moment of use, so even an operator reading the report never sees the customer's raw value.

The limits are honest: the allowlist is explicit but finite (the threat model is accidental mis-action, not a determined adversary).

## 7. Cuts

Depth over breadth, deliberately. **No queues, clusters, or multi-tenant plumbing** (the brief discourages premature infrastructure); **no coordinate/visual clicking** (see §4 — a documented boundary with a known fix, not a shipped capability); **no general commercial sites** (we prove the error taxonomy against a deliberately-hostile local mock *and* two automation-friendly public test sites — SauceDemo and The Internet — rather than targets that sit behind ToS/CAPTCHA/WAF).

Other scoped-out items, each with a known path:

- **Capability deduplication** — collapse "same flow, different parameter" into one task via a flow fingerprint.
- **Automatic error-state discovery** — a negative-testing pass to discover `business_outcome`/`failure` signals automatically (today they're patched in).
- **Generalized output extraction** — extend extraction beyond key-value tables to free-text result pages.
- **Multi-tenant / queue / cluster plumbing** — deliberately omitted; the depth went into the artifact schema, the error taxonomy, and handoff.

Next with more time: canonicalization (`/item/12345` → `/item/:id`) to collapse parameter variants, a cross-tenant override demonstration, and the coordinate-click fallback from §4.

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

**3. The demo target — a "legacy bank" mock.** The local target (`mock-app/`, port 9000) is a deliberately-hostile stand-in for legacy back-office software: 1998-era banking portal, dated DOM, terse labels, no modern JS. It is **multi-modal** — the same endpoint returns a different outcome per input, so a single recorded flow exercises all three result states:

| `member_id` | Result |
|---|---|
| `1001` | JOHN SMITH · ACTIVE · $4,250.00 |
| `1002` | JANE DOE · RESTRICTED · $12.80 (deactivate → access denied) |
| `1003` | ROBERT CHEN · ACTIVE · $18,900 |
| `9999` | no such member |

**4. Statistics report.** Open `http://localhost:8123/` → **Statistics**: per-task **succeeded** (success + business_outcome — every call that returned JSON) vs **failed** (error-outs) rates over *user* calls (admin ops excluded), and a run ledger — succeeded calls show only timing, failures show the *encrypted* input + a one-click **Replay**. **Replay** opens a full result modal (result + extracted JSON + per-step log + screenshot) with an inline **AI optimize** box, so a failing case can be fixed conversationally — optimize runs as a background job.

**5. Implemented stretch goal.** The **agent-facing capability interface** (§8 of the brief) is implemented: the admin console (`/`) exposes saved artifacts as a catalog of callable capabilities, the engine exposes them over `POST /api/run` with typed args, and the user runner (`/user`) demonstrates one being invoked end to end.

**6. Implemented stretch goal — continuous optimization.** The interface is closed into a loop: a maintainer describes a fix in plain English and the LLM returns a validated structured patch as a **long-hold background job**, surfaced back to the page on completion — discover → replay → observe → repair, without hand-editing JSON. It runs on the concurrent (`ThreadingHTTPServer`) dashboard, so a slow optimize never blocks live calls.
