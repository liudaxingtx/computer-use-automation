# Getting Started — Computer-Use Automation System

> **Audience:** an interviewer or first-time user who just cloned the repo and wants to run it, configure it, and judge whether it works — without reading the design internals.

---

## 1. What this is, in one paragraph

interface.ai's agents must operate back-office software that has **no API** — the only way in is to drive the UI like a human. This project is the integration layer that gives those agents hands:

1. **Discover** — an LLM is shown a live browser session and figures out, step by step, how to accomplish a task (e.g. "look up member 1001 and deactivate their account").
2. **Record** — that successful run is distilled into a typed, versioned, reviewable artifact (a *Capability*): *what* to do, *how to locate* each control, and *what to expect* after each step.
3. **Replay** — a deterministic engine re-runs the capability with **no LLM in the loop**, so it is cheap and reliable in production.

The one invariant the whole design protects: **the LLM appears exactly once, during discovery, then leaves the loop.**

---

## 2. Quick start (5 steps)

```bash
# 1. Install dependencies (Python 3.12)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

# 2. Configure your API keys
cp .env.example .env        # then edit .env and fill in the two keys below

# 3. Start the target app (the local "legacy bank" mock, port 9000)
python3 mock-app/server.py

# 4. Start the dashboard (port 8123)
.venv/bin/python -m dashboard.server

# 5. Open http://localhost:8123 in a browser
```

You should see a dark dashboard titled **Automation Tasks** listing four pre-recorded tasks.

---

## 3. Configuration (`.env`)

Only two keys are required; the rest have defaults.

| Variable | Required | What it does | Default |
|---|---|---|---|
| `DEEPSEEK_API_KEY` | ✅ | Drives discovery (observe → decide → act) | — |
| `DEEPSEEK_BASE_URL` | — | OpenAI-compatible endpoint | `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | — | Decision model | `deepseek-v4-pro` |
| `KIMI_API_KEY` | ✅ | Vision fallback for image-only surfaces | — |
| `KIMI_BASE_URL` | — | Use the China endpoint (the `.ai` one returns Invalid Auth) | `https://api.moonshot.cn` |
| `KIMI_MODEL` | — | Vision model (reasoning model, `temperature=1`) | `kimi-k3` |
| `MOCK_URL` | — | The target app under test | `http://localhost:9000/` |

`DEEPSEEK_API_KEY` and `KIMI_API_KEY` come from the environment, never from git — `.env` is git-ignored.

---

## 4. What you'll see: the four pre-recorded tasks

| Task | What it does | Inputs |
|---|---|---|
| `lookup_member` | Search a member by ID and read their detail | `member_id` |
| `deactivate_member` | Search → view detail → deactivate an account | `member_id` |
| `register_operator` | Fill the registration form and submit | `username`, `password`, `email` |
| `login_operator` | Fill the login form and submit | `username`, `password` |

Each task card opens a detail view showing: the **replayable path** (the distilled steps with their locator strategy), the **page screenshots** its path touches, the **extracted output fields**, a **Swagger-style `POST /run`** panel to invoke it with your own inputs, and **recent run history**.

---

## 5. How to verify it works (the success criteria)

### 5.1 The three-state result contract

Every run lands in exactly one of three states — this is the core correctness property:

| State | Meaning | Example |
|---|---|---|
| **success** | Goal reached; structured outputs extracted | `lookup_member` 1001 → `{name: JOHN SMITH, status: ACTIVE, balance: $4,250.00}` |
| **business_outcome** | A *legitimate* expected answer, not a crash | member 9999 → "no such member" |
| **failure** | A hard stop with a debuggable diagnostic | member 1002 → "permission denied" |

### 5.2 Drive it in the browser (the important steps)

1. Open a task (e.g. `lookup_member`), type `1001` into the `member_id` field, click **▶ Execute** → expect **SUCCESS** plus the extracted `name/status/balance` JSON.
2. Type `9999` → expect **BUSINESS OUTCOME** "member not found".
3. For `register_operator`: a fresh username → **SUCCESS**; the same username again → **BUSINESS OUTCOME** "username already registered"; a password under 6 chars → **FAILURE** "password too short".
4. Open **Call log** → see every invocation: how many times each task ran, whether it succeeded, and the exact inputs used (including failures).

### 5.3 One-command test suite

```bash
./scripts/run_tests.sh
```

Boots the mock, then runs the real end-to-end tests (discovery → artifact → replay across all three states → safety/encryption/handoff → vision fallback → observability). It makes real LLM calls, so it costs a few tokens — that is the point: a real run, not a mock.

### 5.4 Inspect the evidence

`evidence/` holds the artifacts of real runs: the distilled capabilities, the raw discovery transcripts, and one replay log per result state. `evidence/runs/` is append-only — every invocation is a dated JSON file (inputs encrypted at rest).

---

## 6. What we built

- **Discovery loop** — accessibility-tree observation (primary) + Kimi K3 vision fallback for image-only surfaces + DeepSeek structured decisions.
- **Artifact model** — a typed, versioned, human-reviewable `Capability` (diffable JSON, not a model monologue).
- **Deterministic replay** — act → assert → branch, with the three-state result contract, bounded retries for recoverable conditions, and hard-stop escalation.
- **Data extraction** — on success, the replay reads the result page back into structured JSON (normalized field matching: `member_id` == `MEMBER ID`).
- **One-click recording** — `POST /api/discover`: give a URL + a plain-English description, get a recorded + verified task back. Auto-infers checkpoint, inputs (named after form fields), and start page.
- **Observability** — append-only run store, success telemetry, a failure inbox, and the replay-the-error → repair → re-verify loop.
- **Safety** — action allowlist (enforced in discovery *and* replay), AES-256-GCM encryption of customer inputs at rest, and a human handoff state machine (pause / cede / resume on the live session).
- **Task management dashboard** — grouped by domain, per-task locator detail + screenshots, Swagger-style invoke, call log, and delete (with confirm).

---

## 7. Design decisions worth knowing

These are the "why" behind the "what" — the parts we considered carefully:

- **The model discovers; the artifact replays.** Separating discovery from replay is what makes this a design rather than "a Playwright wrapper." Production runs never pay for a model.
- **Intent, not coordinates.** We record *how to locate* a control (role + name), never pixels. An artifact recorded on one institution's instance applies to another, differently-branded one.
- **Every step asserts.** We never assume a click worked; each step declares what it expects, and a mismatch is classified.
- **Errors are first-class.** Three states, not two: "no such member" is a *business outcome*, not a failure. This taxonomy is proven against a deliberately hostile mock.
- **Non-idempotent operations are recognized.** Recording `register` re-runs the same input on verify, which correctly surfaces "username taken" — a side-effect, not a bug.
- **Data is encrypted at rest.** Customer-entered values are AES-256-GCM encrypted with a per-tenant key; locator structure stays plaintext so artifacts remain reviewable.

---

## 8. Considered but not yet built

Each of these was deliberately scoped out, with a known path to add it — not blind gaps:

- **Coordinate/visual clicking.** The schema already reserves a `visual` locator strategy and the vision model already *locates* non-semantic controls — but the act vocabulary has no coordinate action. Only matters for canvas/image-captcha surfaces.
- **Capability deduplication.** Auto-recording doesn't yet collapse "the same flow with a different parameter" into one task (a *flow fingerprint* would fix this).
- **Automatic error-state discovery.** Recording only sees the happy path; `business_outcome` / `failure` signals are patched in today. A negative-testing pass could discover them automatically.
- **Generalized output extraction.** Today extraction targets key-value tables; free-text result pages need an extra extraction strategy (CSS selector is already reserved).
- **Multi-tenant / queue / cluster plumbing.** Deliberately omitted — the brief discourages premature infrastructure; the depth went into the artifact schema, the error taxonomy, and handoff instead.
- **Automation against public sites.** Omitted on ToS and state-control grounds; the error taxonomy is proven on a local hostile mock.

---

## 9. Where the deep docs live

| Doc | For |
|---|---|
| `GETTING_STARTED.md` | **You are here** — run, configure, verify |
| `REPORT.md` | The formal submission write-up (the seven mandated headings) |
| `DESIGN.md` | The full working design record and implementation plan |
| `TODO.md` | Progress tracker |
