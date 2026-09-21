"""Task (capability) dashboard — a management console for the computer-use system.

Lists every recorded capability as a *task*, shows its replayable path (the
distilled steps with their locator strategy), the page screenshots its path
touches, and lets you fill in inputs and run a replay to test it live.

Endpoints:
  GET  /api/tasks               -> all tasks + a completion summary
  GET  /api/task/<name>         -> full detail: steps (locators), inputs, screenshots
  POST /api/run                 -> {"task": name, "inputs": {...}} -> run a replay
  GET  /api/screenshots/<file>  -> a page / result screenshot

Run from the repo root:  .venv/bin/python -m dashboard.server
"""
import hashlib
import json
import re
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.crypto import decrypt, derive_key, master_key_from_env  # noqa: E402
from agent.observability import ARTIFACT_DIR, EVIDENCE_DIR, RUNS_DIR, ReplayStore  # noqa: E402

INDEX = ROOT / "dashboard" / "index.html"
USER_INDEX = ROOT / "dashboard" / "user.html"
SHOTS = ROOT / "dashboard" / "screenshots"
USERS_DIR = ROOT / "users"
PORT = 8123
MOCK_URL = "http://localhost:9000/"

# --- Per-user login & isolation ---------------------------------------------
# The /user runner is login-gated. Each user has a private folder users/<id>/
# holding their identity (password_hash) and their legacy-app credentials
# (password AES-256-GCM encrypted under a per-user key). A logged-in session is
# a random token -> user_id in memory; replays carry the user_id so the audit
# log can answer "who did this", and the legacy credential is injected from the
# user's own folder (never a shared service account).
_SESSIONS: dict[str, str] = {}
_SESSIONS_LOCK = threading.Lock()
_SALT = "demo-salt"


def _hash_password(pw: str) -> str:
    return "sha256:" + hashlib.sha256((_SALT + pw).encode()).hexdigest()


def _load_profile(user_id: str) -> dict | None:
    p = USERS_DIR / user_id / "profile.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _load_credentials(user_id: str) -> dict | None:
    p = USERS_DIR / user_id / "credentials.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _authenticate(username: str, password: str) -> str | None:
    prof = _load_profile(username)
    if prof and prof.get("password_hash") == _hash_password(password):
        return username
    return None


def _new_session(user_id: str) -> str:
    token = secrets.token_hex(16)
    with _SESSIONS_LOCK:
        _SESSIONS[token] = user_id
    return token


def _session_user(token: str | None) -> str | None:
    if not token:
        return None
    with _SESSIONS_LOCK:
        return _SESSIONS.get(token)


def _drop_session(token: str | None) -> None:
    if not token:
        return
    with _SESSIONS_LOCK:
        _SESSIONS.pop(token, None)


def _legacy_credentials(user_id: str) -> dict | None:
    """Return this user's legacy-app credentials with the password decrypted.
    The per-user key means one user's folder cannot decrypt another's."""
    creds = _load_credentials(user_id)
    if not creds:
        return None
    key = derive_key(f"user:{user_id}", master_key_from_env())
    try:
        return {
            "legacy_username": creds.get("legacy_username"),
            "legacy_password": decrypt(creds["legacy_password"], key),
        }
    except Exception:
        return None


def _inject_credentials(cap, inputs: dict, user_id: str | None) -> dict:
    """Fill a task's username/password inputs from the logged-in user's own
    credential folder — never a shared account. Only inject when the input is
    not already supplied, and only for inputs the task actually declares."""
    if not user_id:
        return inputs
    creds = _legacy_credentials(user_id)
    if not creds:
        return inputs
    declared = {i.name for i in cap.inputs}
    out = dict(inputs)
    if "username" in declared and not out.get("username"):
        out["username"] = creds["legacy_username"]
    if "password" in declared and not out.get("password"):
        out["password"] = creds["legacy_password"]
    return out


def _resolve_user_data(user_id: str | None, path: str):
    """Resolve a dotted path (e.g. "userBO.eeID") against the user's private
    profile. Returns the leaf value, or None if the path is absent."""
    if not user_id or not path:
        return None
    prof = _load_profile(user_id)
    if not prof:
        return None
    node = prof
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _bind_user_data(cap, inputs: dict, user_id: str | None) -> tuple[dict, list[str]]:
    """Auto-fetch inputs declared `bind: "<path>"` from the calling user's own
    private data (profile.json). Returns (inputs, missing_bindings).

    A bound input is ALWAYS taken from the user's folder — never from the
    request body — so a runner cannot spoof their own member id / employee id.
    This is the "成熟的 solution 不暴露自动抓取值" rule: the runner never sees
    or types a bound input; the server fills it from the user's private record."""
    if not user_id:
        return inputs, []
    out = dict(inputs)
    missing = []
    for spec in cap.inputs:
        if not spec.bind:
            continue
        val = _resolve_user_data(user_id, spec.bind)
        if val is None:
            missing.append(f"{spec.name} (path {spec.bind})")
            continue
        out[spec.name] = val
    return out, missing


def _userdata_schema() -> dict:
    """The set of bindable fields across all seeded users, as dotted paths
    (e.g. "userBO.eeID"). The admin console uses this to offer a pick-list when
    binding a task input to user data."""
    fields: set[str] = set()
    if USERS_DIR.exists():
        for p in USERS_DIR.glob("*/profile.json"):
            try:
                prof = json.loads(p.read_text())
            except Exception:
                continue
            for ns, obj in prof.items():
                if isinstance(obj, dict):
                    for k in obj:
                        fields.add(f"{ns}.{k}")
    return {"fields": sorted(fields)}

# The mock pages each capability's replay path can touch, in order.
# Keyed by capability name so each task shows only the pages it actually visits.
SHOT_MAP = {
    "deactivate_member": [
        ("search", "Search page"),
        ("detail", "Member detail"),
        ("confirm", "Deactivation confirm"),
        ("success", "Result — success"),
        ("no_such_member", "Result — no such member"),
        ("access_denied", "Result — access denied"),
    ],
    "lookup_member": [
        ("search", "Search page"),
        ("detail", "Member detail"),
        ("no_such_member", "Result — no such member"),
    ],
    "register_operator": [
        ("register", "Registration form"),
        ("register_success", "Result — registration success"),
        ("register_taken", "Result — username taken"),
        ("register_invalid", "Result — invalid password"),
    ],
    "login_operator": [
        ("login", "Login form"),
        ("login_success", "Result — login success"),
        ("login_invalid", "Result — invalid credentials"),
    ],
    "saucedemo_login": [
        ("sauce_login_form", "Login page"),
        ("sauce_login_success", "Result — products (logged in)"),
        ("sauce_login_invalid", "Result — invalid credentials"),
    ],
    "saucedemo_checkout": [
        ("sauce_checkout_products", "Products — add to cart"),
        ("sauce_checkout_cart", "Shopping cart"),
        ("sauce_checkout_form", "Checkout — shipping form"),
        ("sauce_checkout_overview", "Checkout — order overview"),
        ("sauce_checkout_complete", "Result — order complete"),
    ],
    "theinternet_login": [
        ("inet_login_form", "Login page"),
        ("inet_login_success", "Result — secure area"),
        ("inet_login_invalid", "Result — invalid credentials"),
    ],
}


def _load_capability(name: str):
    from agent.artifact import Capability
    # The artifact is the recorded solution path. artifacts/ is version-controlled,
    # so there is no fallback — it is the single source of truth.
    return Capability.model_validate_json((ARTIFACT_DIR / f"{name}.json").read_text())


def _capabilities() -> list[dict]:
    # List the version-controlled solution paths (artifacts/). No evidence-seed
    # fallback — artifacts/ is committed.
    names: set[str] = set()
    if ARTIFACT_DIR.exists():
        names.update(f.stem for f in ARTIFACT_DIR.glob("*.json"))
    out = []
    for name in sorted(names):
        try:
            out.append(_load_capability(name))
        except Exception:
            continue
    return out


def _run_stats(name: str) -> dict:
    # Only user calls count, matching the statistics report — admin Execute /
    # Replay ops are operations, not real traffic.
    stats = {"success": 0, "business_outcome": 0, "failure": 0, "total": 0}
    for run in ReplayStore().list_runs():
        if run.capability == name and run.source == "user":
            stats[run.result] += 1
            stats["total"] += 1
    return stats


def _task_status(cap) -> str:
    """verified / unverified / error, judged only by runs since the last optimize.

    - error:      any failure (no JSON feedback) since the last optimize — broken.
    - verified:   at least one success since the last optimize.
    - unverified: no success and no failure since the last optimize (just optimized).
    """
    optimized_at = cap.meta.optimized_at or cap.meta.created_at
    has_success = has_failure = False
    for run in ReplayStore().list_runs():
        if run.capability != cap.meta.name:
            continue
        if run.started_at <= optimized_at:
            continue
        if run.result == "failure":
            has_failure = True
        elif run.result == "success":
            has_success = True
    if has_failure:
        return "error"
    if has_success:
        return "verified"
    return "unverified"


def _task_summary(cap) -> dict:
    stats = _run_stats(cap.meta.name)
    status = _task_status(cap)
    return {
        "name": cap.meta.name,
        "version": cap.meta.version,
        "description": cap.meta.description,
        "domain": cap.meta.domain,
        "steps_count": len(cap.steps),
        "inputs": [i.model_dump() for i in cap.inputs],
        "status": status,
        "verified": status == "verified",
        "runs": stats,
    }


def _task_detail(cap) -> dict:
    steps = []
    for s in cap.steps:
        target = s.target.model_dump() if s.target else None
        steps.append({
            "action": s.action,
            "value": s.value,
            "target": target,
            "assertion": s.assertion.model_dump() if s.assertion else None,
            "on_error": s.on_error.model_dump() if s.on_error else None,
        })
    return {
        "name": cap.meta.name,
        "version": cap.meta.version,
        "description": cap.meta.description,
        "domain": cap.meta.domain,
        "inputs": [i.model_dump() for i in cap.inputs],
        "outputs": [o.model_dump() for o in cap.outputs],
        "checkpoint": cap.checkpoint,
        "checkpoint_text": cap.checkpoint_text,
        "business_outcomes": [b.model_dump() for b in cap.business_outcomes],
        "failure_patterns": [f.model_dump() for f in cap.failure_patterns],
        "examples": [e.model_dump() for e in cap.examples],
        "steps": steps,
        "screenshots": [
            {"id": pid, "label": label, "url": f"/api/screenshots/{pid}.png"}
            for pid, label in SHOT_MAP.get(cap.meta.name, [])
            if (SHOTS / f"{pid}.png").exists()
        ],
        "runs": _run_stats(cap.meta.name),
    }


def _execute_replay(cap, inputs: dict, name: str, echo_inputs: bool = True,
                    source: Literal["user", "admin"] = "user",
                    expect: dict | None = None, user_id: str | None = None) -> dict:
    from playwright.sync_api import sync_playwright

    from agent.replay import assert_outputs, replay

    shot = f"run_{name}.png"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(cap.start_url or MOCK_URL)
        run = replay(page, cap, inputs=inputs, screenshot_dir=str(SHOTS))
        page.screenshot(path=str(SHOTS / shot))
        browser.close()
    run.source = source
    run.expect = expect or None
    run.user_id = user_id

    # Output assertion: when an `expect` is supplied, a returned JSON that
    # doesn't satisfy it is a HARD FAILURE — not a success. A JSON that looks
    # fine but fails the assertion means the task itself is broken (wrong
    # extractor, wrong success signal, wrong business outcome), so we override
    # whatever the checkpoint/outcome classifier decided.
    expect_ok = None
    if expect:
        ok, reason = assert_outputs(run.outputs, expect)
        expect_ok = ok
        if not ok:
            run.result = "failure"
            run.diagnostic = f"expect assertion failed: {reason}"

    # Persist every invocation so the statistics report can answer "how many
    # times was this task really used, and what happened".
    ReplayStore().record(run)
    return {
        "task": name,
        "result": run.result,
        "diagnostic": run.diagnostic,
        "outputs": run.outputs,
        "expect": run.expect,
        "expect_ok": expect_ok,
        "screenshot": f"/api/screenshots/{shot}?t={int(time.time() * 1000)}",
        "steps": run.steps,
        "inputs": inputs if echo_inputs else None,
        "duration_ms": run.duration_ms,
    }


def _run_task(name: str, inputs: dict, source: Literal["user", "admin"] = "user",
              expect: dict | None = None, user_id: str | None = None) -> dict:
    cap = _load_capability(name)
    # Inject the calling user's own legacy credential (username/password) from
    # their private folder, so a replay never falls back to a shared account.
    inputs = _inject_credentials(cap, inputs, user_id)
    # Auto-fetch bound inputs (e.g. member_id <- userBO.eeID) from the user's
    # own folder. A bound input that can't be resolved is a pre-flight config
    # error — the run never starts, so nothing is recorded.
    inputs, missing = _bind_user_data(cap, inputs, user_id)
    if missing:
        return {"error": "bound input(s) could not be resolved from your account "
                         "data: " + "; ".join(missing)}
    return _execute_replay(cap, inputs, name, source=source, expect=expect,
                           user_id=user_id)


def _rerun_task(run_id: str) -> dict:
    """Re-run a recorded invocation with its original inputs. Inputs are
    decrypted only at the moment of replay (never exposed by the report); the
    re-run is itself recorded as a new invocation (an admin op, not a user call).
    If the original was a failure and the re-run succeeds, the original is
    stamped as resolved (failure → new result)."""
    store = ReplayStore()
    run = store.load(run_id, decrypt=True)
    cap = _load_capability(run.capability)
    result = _execute_replay(cap, run.inputs, run.capability,
                             echo_inputs=False, source="admin", expect=run.expect)
    result["rerun_of"] = run_id

    # A successful re-run of a previously-failed invocation proves the fix:
    # mark the original record resolved with the new result.
    if run.result == "failure" and result["result"] in ("success", "business_outcome"):
        store.record_resolution(run_id, result["result"])

    return result


def _runs_json() -> dict:
    """Call-log for the monitoring view: every recorded run, newest first, with
    decrypted inputs, plus aggregate stats (total / success / business / failure).
    """
    store = ReplayStore()
    entries = []
    if store.directory.exists():
        for f in sorted(store.directory.glob("*.json")):
            try:
                run = store.load(f.stem, decrypt=True)
            except Exception:
                continue
            entries.append((f.stem, run))
    entries.sort(key=lambda x: x[1].started_at, reverse=True)

    runs = []
    for run_id, run in entries:
        runs.append({
            "id": run_id,
            "capability": run.capability,
            "version": run.version,
            "result": run.result,
            "inputs": run.inputs,
            "outputs": run.outputs,
            "diagnostic": run.diagnostic,
            "started_at": run.started_at.isoformat(),
            "duration_ms": run.duration_ms,
        })

    by_task: dict[str, dict] = {}
    for r in runs:
        s = by_task.setdefault(r["capability"],
                               {"total": 0, "success": 0, "business_outcome": 0, "failure": 0})
        s["total"] += 1
        s[r["result"]] += 1

    return {
        "total": len(runs),
        "success": sum(1 for r in runs if r["result"] == "success"),
        "business_outcome": sum(1 for r in runs if r["result"] == "business_outcome"),
        "failure": sum(1 for r in runs if r["result"] == "failure"),
        "by_task": by_task,
        "runs": runs,
    }


def _stats_json() -> dict:
    """Statistics report for the admin console (replaces the flat call log).

    Counts only *user* calls (source == "user"); admin Execute/Replay ops are
    excluded. Aggregated per task: total + success / business-outcome / failure
    counts and rates. The run ledger shows timing for successes (their data is
    not stored — we only care that they succeeded), and the *encrypted* input
    (never decrypted here) + diagnostic for failures, with a run id so a
    maintainer can replay the exact failing invocation.
    """
    store = ReplayStore()
    entries = []
    if store.directory.exists():
        for f in sorted(store.directory.glob("*.json")):
            try:
                entries.append((f.stem, store.load(f.stem, decrypt=False)))
            except Exception:
                continue
    entries.sort(key=lambda x: x[1].started_at, reverse=True)
    # Only user calls count; admin ops (Execute/Replay) are excluded outright.
    entries = [(rid, r) for rid, r in entries if r.source == "user"]

    by_task = {}
    for run_id, run in entries:
        s = by_task.setdefault(run.capability, {
            "capability": run.capability,
            "version": run.version,
            "success": 0, "business_outcome": 0, "failure": 0, "total": 0,
            "runs": [],
        })
        s[run.result] += 1
        s["total"] += 1
        entry = {
            "id": run_id,
            "result": run.result,
            "resolved_result": run.resolved_result,
            "resolved_at": run.resolved_at.isoformat() if run.resolved_at else None,
            "started_at": run.started_at.isoformat(),
            "duration_ms": run.duration_ms,
            "diagnostic": run.diagnostic,
        }
        if run.result == "failure":
            # Encrypted input only — the report never decrypts customer data.
            entry["inputs_encrypted"] = run.inputs
        s["runs"].append(entry)

    total = len(entries)
    n_success = sum(1 for _, r in entries if r.result == "success")
    n_business = sum(1 for _, r in entries if r.result == "business_outcome")
    n_failure = sum(1 for _, r in entries if r.result == "failure")

    def _rate(n):
        return round(n / total, 3) if total else 0.0

    # A call "succeeded" if it returned a usable JSON result — either a clean
    # success or a business_outcome (e.g. wrong password -> explicit JSON). Only a
    # hard failure (mid-run error out, no JSON returned) counts as failure.
    n_succeeded = n_success + n_business

    tasks = []
    for name in sorted(by_task):
        s = dict(by_task[name])
        s["succeeded"] = s["success"] + s["business_outcome"]
        s["success_rate"] = round(s["succeeded"] / s["total"], 3)
        s["business_rate"] = round(s["business_outcome"] / s["total"], 3)
        s["failure_rate"] = round(s["failure"] / s["total"], 3)
        tasks.append(s)

    return {
        "total": total,
        "success": n_success,
        "business_outcome": n_business,
        "failure": n_failure,
        "succeeded": n_succeeded,
        "success_rate": _rate(n_succeeded),
        "business_rate": _rate(n_business),
        "failure_rate": _rate(n_failure),
        "by_task": tasks,
    }


def _bump_version(v: str) -> str:
    """Patch-bump a semver string (1.0.0 -> 1.0.1). Non-semver is left untouched."""
    parts = (v or "").split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return v
    major, minor, patch = (int(p) for p in parts)
    return f"{major}.{minor}.{patch + 1}"


def _optimize_task(name: str, instruction: str) -> dict:
    """AI-assisted artifact tuning. Pass the current artifact + a maintainer's
    natural-language instruction to the decision LLM, which returns a structured
    patch (outputs / checkpoint / business_outcomes / failure_patterns). Apply it
    (pydantic-validated), persist, and report what changed — so a maintainer can
    fix a bad output extractor or a wrong success signal by just describing it."""
    from agent.llm import decide

    cap = _load_capability(name)

    recent = []
    for run in ReplayStore().list_runs():
        if run.capability == name:
            recent.append({
                "result": run.result,
                "diagnostic": run.diagnostic,
                "outputs": run.outputs,
                "started_at": run.started_at.isoformat(),
            })
    recent = recent[-5:]

    system = (
        "You maintain computer-use automation artifacts (a 'Capability'). "
        "Given the current artifact and a maintainer's instruction, return a JSON "
        "object describing exactly which fields to change. Include ONLY fields you "
        "are actually changing; omit everything else.\n\n"
        "Return shape:\n"
        '{\n'
        '  "explanation": "one or two sentences on what you changed and why",\n'
        '  "outputs": [{"name": str, "type": "str", "source": str, "label": str, "extract": str}],\n'
        '  "checkpoint_text": "exact page text that signals success",\n'
        '  "checkpoint": "human-readable goal",\n'
        '  "business_outcomes": [{"text": str, "label": str}],\n'
        '  "failure_patterns": [{"text": str, "label": str}]\n'
        "}\n\n"
        "Rules:\n"
        "- outputs / business_outcomes / failure_patterns are FULL replacement lists: "
        "return the COMPLETE final list you want, not just the changes. To add an item, "
        "include it alongside the existing ones; to remove an item, omit it. Include every "
        "item you want to keep.\n"
        "- outputs[].extract must be a real CSS selector (e.g. \"h2\", \"#flash\", \".title\") "
        "whenever the value is read from a specific element; leave it \"\" only for key-value "
        "table extraction where label matches the on-page field.\n"
        "- Outputs must reflect what the result page ACTUALLY shows. When the result page is "
        "plain text (no <table>) — e.g. a success message rendered in <font>/<b> tags — use a "
        "real CSS selector in extract (e.g. \"font b\" for the headline, \"font[size=2]\" for a "
        "message line) instead of relying on table extraction, which silently yields null.\n"
        "- Never declare an output the page does not echo back. If a result page does not "
        "re-display a field (e.g. a password or email the form accepted but the page does not "
        "show again), drop it from outputs rather than leaving it to extract as null.\n"
        "- Pick a selector that uniquely identifies the target. A result page often carries a "
        "site header / nav with the same tags (e.g. several <font><b> elements), and extract "
        "takes the FIRST match — so prefer a discriminating attribute (font[color=\"#008000\"], "
        "font[size=\"4\"]) over a bare tag (font b).\n"
        "- checkpoint_text must be literal text that appears on the page on success.\n"
        "- business_outcomes are legitimate expected answers (e.g. \"no such member\"); "
        "failure_patterns are hard errors (e.g. \"access denied\").\n"
        "- Design a standardized output: outputs[].name are the keys of the success JSON "
        "object (each with a clear type), and each business_outcome returns "
        "{\"outcome\": label} — so every result case has a well-defined JSON shape.\n"
        "- Do not invent fields; only change what the instruction asks for."
    )

    user = (
        f"Current artifact:\n{cap.model_dump_json(indent=2)}\n\n"
        f"Recent run results:\n{json.dumps(recent, indent=2, default=str)}\n\n"
        f"Maintainer instruction:\n{instruction}\n"
    )

    result = decide([
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ])

    explanation = result.get("explanation", "")
    patch = {k: v for k, v in result.items()
             if k != "explanation" and v is not None and v != ""}

    # List fields (outputs / business_outcomes / failure_patterns) are a FULL
    # replacement: the prompt asks the LLM for the complete final list, so apply
    # it verbatim. Pydantic validation below rejects any malformed item.
    if not patch:
        return {"ok": True, "name": name, "explanation": explanation,
                "changed": [], "note": "no fields changed"}

    # Validate through the schema (raises on a bad patch) and persist.
    from agent.artifact import Capability
    try:
        new_cap = Capability.model_validate({**cap.model_dump(), **patch})
    except Exception as e:  # noqa: BLE001 — surface an invalid AI patch, don't crash
        return {"ok": False, "error": f"AI returned an invalid patch: {e}",
                "explanation": explanation}
    # Optimizing changes the solution path, so any prior verified/error status is
    # reset: record optimized_at and let status be re-earned by fresh runs.
    # Every optimize also patch-bumps the version so a fixed task is a new,
    # distinguishable artifact (1.0.0 -> 1.0.1) — callers and the run log can
    # tell which version a run executed against.
    from datetime import datetime, timezone
    new_cap.meta.optimized_at = datetime.now(timezone.utc)
    new_cap.meta.version = _bump_version(new_cap.meta.version)
    (ARTIFACT_DIR / f"{name}.json").write_text(new_cap.model_dump_json(indent=2))

    return {
        "ok": True,
        "name": name,
        "version": new_cap.meta.version,
        "explanation": explanation,
        "changed": list(patch.keys()),
        "outputs": [o.model_dump() for o in new_cap.outputs],
        "checkpoint_text": new_cap.checkpoint_text,
    }


# --- Background jobs (AI optimize + discovery) -------------------------------
# Long-running LLM tasks run in a background thread so a maintainer can close
# the tab (or navigate away) and the LLM still finishes; the admin console polls
# the registry and is notified when a job lands. One mechanism serves both AI
# optimize and new-task discovery. The registry is guarded by a lock because
# ThreadingHTTPServer serves concurrent requests.
_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()
_MAX_JOBS = 50


def _submit_job(job_type: str, name: str, runner, payload: dict) -> dict:
    """Submit a background job. Returns immediately with a job id; a worker
    thread persists the outcome for later polling."""
    job_id = f"{job_type}_{int(time.time() * 1000)}_{secrets.token_hex(3)}"
    with _JOBS_LOCK:
        _JOBS[job_id] = {
            "job_id": job_id,
            "type": job_type,
            "name": name,
            "status": "running",
            "started_at": time.time(),
            "finished_at": None,
            "result": None,
            "error": None,
        }
    threading.Thread(target=_job_worker, args=(job_id, runner, payload),
                     daemon=True).start()
    return {"ok": True, "job_id": job_id, "type": job_type, "name": name,
            "status": "running"}


def _job_worker(job_id: str, runner, payload: dict) -> None:
    """Background worker: run the slow task and record the outcome. Runs to
    completion even if the submitting browser tab is closed."""
    try:
        result = runner(**payload)
        with _JOBS_LOCK:
            _JOBS[job_id]["status"] = "done"
            _JOBS[job_id]["result"] = result
            _JOBS[job_id]["finished_at"] = time.time()
    except Exception as e:  # noqa: BLE001 — surface worker failure to the poller
        with _JOBS_LOCK:
            _JOBS[job_id]["status"] = "error"
            _JOBS[job_id]["error"] = str(e)
            _JOBS[job_id]["finished_at"] = time.time()


def _optimize_async(name: str, instruction: str) -> dict:
    """Submit an AI optimize as a long-running job."""
    return _submit_job("optimize", name, _optimize_task,
                       {"name": name, "instruction": instruction})


def _job_status(job_id: str) -> dict:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return {"ok": False, "error": f"job '{job_id}' not found"}
        return dict(job)


def _jobs() -> dict:
    """All known jobs (newest first) for the admin's global indicator. Prune
    old finished jobs so the in-memory registry never grows unbounded."""
    with _JOBS_LOCK:
        if len(_JOBS) > _MAX_JOBS:
            finished = sorted(
                (j for j in _JOBS.values() if j["status"] in ("done", "error")),
                key=lambda j: j["finished_at"] or 0,
            )
            for j in finished[: len(_JOBS) - _MAX_JOBS]:
                _JOBS.pop(j["job_id"], None)
        jobs = list(_JOBS.values())
    jobs.sort(key=lambda j: j["started_at"], reverse=True)
    return {"jobs": jobs}


def _delete_task(name: str) -> dict:
    """Delete a recorded task and everything tied to it: the artifact, the
    discovery transcript, and every recorded run (call history).
    Returns what was removed so the UI can confirm the result."""
    if not (ARTIFACT_DIR / f"{name}.json").exists():
        return {"ok": False, "error": f"task '{name}' not found"}

    removed = []
    for p in (ARTIFACT_DIR / f"{name}.json",
              EVIDENCE_DIR / f"discovery_{name}.json"):
        if p.exists():
            p.unlink()
            removed.append(p.name)

    runs_removed = 0
    if RUNS_DIR.exists():
        for f in RUNS_DIR.glob(f"{name}__*.json"):
            f.unlink()
            runs_removed += 1

    for f in SHOTS.glob(f"run_{name}.png"):
        if f.exists():
            f.unlink()

    return {
        "ok": True,
        "name": name,
        "files_removed": len(removed) + runs_removed,
        "runs_removed": runs_removed,
    }


def _reset_runs() -> dict:
    """Delete every recorded run, clearing the statistics report — a clean slate
    for a fresh test run. Returns how many runs were removed."""
    removed = 0
    if RUNS_DIR.exists():
        for f in RUNS_DIR.glob("*.json"):
            f.unlink()
            removed += 1
    return {"ok": True, "removed": removed}


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:40]


def _rename_task(old_name: str, new_name: str) -> dict:
    """Rename a task and everything tied to it: the artifact file + meta.name,
    the discovery transcript, every recorded run (filename + capability field),
    and its screenshots."""
    old_name = (old_name or "").strip()
    new_name = (new_name or "").strip()
    if not old_name or not new_name:
        return {"ok": False, "error": "old and new names are required"}
    new_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", new_name).strip("_")[:60]
    if not new_name:
        return {"ok": False, "error": "name must contain at least one letter or digit"}
    if new_name == old_name:
        return {"ok": True, "name": new_name, "changed": False}
    if not (ARTIFACT_DIR / f"{old_name}.json").exists():
        return {"ok": False, "error": f"task '{old_name}' not found"}
    if (ARTIFACT_DIR / f"{new_name}.json").exists():
        return {"ok": False, "error": f"a task named '{new_name}' already exists"}

    # artifact — rewrite under the new name with meta.name updated
    cap = _load_capability(old_name)
    cap.meta.name = new_name
    (ARTIFACT_DIR / f"{new_name}.json").write_text(cap.model_dump_json(indent=2))
    (ARTIFACT_DIR / f"{old_name}.json").unlink()

    # discovery transcript
    disc_old = EVIDENCE_DIR / f"discovery_{old_name}.json"
    if disc_old.exists():
        disc_old.rename(EVIDENCE_DIR / f"discovery_{new_name}.json")

    # runs — patch the capability field, then rename the file prefix
    if RUNS_DIR.exists():
        for f in list(RUNS_DIR.glob(f"{old_name}__*.json")):
            try:
                data = json.loads(f.read_text())
                data["capability"] = new_name
                f.write_text(json.dumps(data, indent=2))
            except Exception:
                pass
            f.rename(RUNS_DIR / f"{new_name}__{f.name.split('__', 1)[1]}")

    # screenshots — the verification shot and any SHOT_MAP entry
    for f in list(SHOTS.glob(f"run_{old_name}.png")):
        f.rename(SHOTS / f"run_{new_name}.png")
    if old_name in SHOT_MAP:
        SHOT_MAP[new_name] = SHOT_MAP.pop(old_name)

    return {"ok": True, "name": new_name, "changed": True}


def _discover_task(url: str, task: str, name: str = "") -> dict:
    """Run a full discovery -> auto-record -> verify pipeline for a new task.

    Given a start URL and a natural-language description, drives the LLM through
    the site to discover the flow, distills it into a Capability (no hand-written
    metadata), persists it, then immediately replays it once to verify.
    """
    from playwright.sync_api import sync_playwright

    from agent.artifact import auto_serialize, infer_params
    from agent.loop import run_discovery
    from agent.replay import replay

    url = url.strip() or MOCK_URL
    task = (task or "").strip()
    if not task:
        raise ValueError("task description is required")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url)
        discovery = run_discovery(page, task)
        browser.close()

    if discovery.get("status") != "success":
        return {
            "ok": False,
            "error": f"discovery ended with '{discovery.get('status')}': {discovery.get('reason', '')}",
            "steps": len(discovery.get("steps", [])),
        }

    name = (name or "").strip() or (_slugify(task) or "task")
    if (ARTIFACT_DIR / f"{name}.json").exists():
        name = f"{name}_{int(time.time())}"

    cap = auto_serialize(discovery, name=name, description=task, url=url)
    if not cap.checkpoint_text:
        return {
            "ok": False,
            "error": "the LLM did not declare a success signal (checkpoint_text) — "
                     "could not auto-record; please rephrase the task description.",
            "steps": len(cap.steps),
        }

    # persist the artifact (solution path) + the raw discovery transcript
    ARTIFACT_DIR.mkdir(exist_ok=True)
    EVIDENCE_DIR.mkdir(exist_ok=True)
    (ARTIFACT_DIR / f"{name}.json").write_text(cap.model_dump_json(indent=2))
    (EVIDENCE_DIR / f"discovery_{name}.json").write_text(
        json.dumps(discovery, indent=2, default=str)
    )

    # immediate verification replay using the discovered input values
    _, value_params = infer_params(discovery)
    verify_inputs = {pname: val for val, pname in value_params.items()}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(cap.start_url or url)
        run = replay(page, cap, inputs=verify_inputs)
        page.screenshot(path=str(SHOTS / f"run_{name}.png"))
        browser.close()
    run.source = "admin"  # the record-time verification is an admin op
    ReplayStore().record(run)

    return {
        "ok": True,
        "name": name,
        "url": url,
        "steps": len(cap.steps),
        "checkpoint_text": cap.checkpoint_text,
        "inputs": [i.model_dump() for i in cap.inputs],
        "outputs": [o.model_dump() for o in cap.outputs],
        "verify_result": run.result,
        "verify_outputs": run.outputs,
        "verify_diagnostic": run.diagnostic,
        "screenshot": f"/api/screenshots/run_{name}.png?t={int(time.time() * 1000)}",
    }


def _discover_async(url: str, task: str, name: str) -> dict:
    """Submit a new-task discovery as a long-running job."""
    return _submit_job("discover", (name or task).strip() or "new task",
                       _discover_task, {"url": url, "task": task, "name": name})


def _edit_examples(name: str, action: str, index: int, example: dict) -> dict:
    """Add / update / remove a runnable example on a task, so a maintainer can
    accumulate a library of fixed, verified input→result cases over time."""
    from agent.artifact import Example
    cap = _load_capability(name)
    examples = list(cap.examples)
    if action == "add":
        examples.append(Example(**example))
    elif action in ("update", "remove"):
        if not (0 <= index < len(examples)):
            return {"ok": False, "error": f"example index {index} out of range"}
        if action == "update":
            examples[index] = Example(**example)
        else:
            examples.pop(index)
    else:
        return {"ok": False, "error": f"unknown action '{action}'"}
    cap.examples = examples
    (ARTIFACT_DIR / f"{name}.json").write_text(cap.model_dump_json(indent=2))
    return {"ok": True, "examples": [e.model_dump() for e in cap.examples]}


def _set_bind(name: str, input_name: str, bind: str) -> dict:
    """Set (or clear) a task input's `bind` path — the per-user data field it is
    auto-fetched from (e.g. "userBO.eeID"). This is how a maintainer declares
    that an input is private to each user and must not be typed by the runner.

    Changing a binding changes the solution contract, so — like an optimize —
    the version is patch-bumped and the verified/error status is reset (a
    previously-verified run no longer proves the new binding)."""
    cap = _load_capability(name)
    target = None
    for spec in cap.inputs:
        if spec.name == input_name:
            target = spec
            break
    if target is None:
        return {"ok": False, "error": f"input '{input_name}' not found on task '{name}'"}
    new_bind = (bind or "").strip()
    target.bind = new_bind
    from datetime import datetime, timezone
    cap.meta.version = _bump_version(cap.meta.version)
    cap.meta.optimized_at = datetime.now(timezone.utc)
    (ARTIFACT_DIR / f"{name}.json").write_text(cap.model_dump_json(indent=2))
    return {"ok": True, "name": name, "input": input_name, "bind": new_bind,
            "version": cap.meta.version,
            "inputs": [i.model_dump() for i in cap.inputs]}



def build_tasks() -> dict:
    caps = _capabilities()
    tasks = [_task_summary(c) for c in caps]
    return {
        "tasks": tasks,
        "summary": {
            "total": len(tasks),
            "verified": sum(1 for t in tasks if t["status"] == "verified"),
            "unverified": sum(1 for t in tasks if t["status"] == "unverified"),
            "error": sum(1 for t in tasks if t["status"] == "error"),
        },
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, ctype: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status: int = 200) -> None:
        self._send(json.dumps(obj, indent=2).encode(), "application/json", status)

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n) if n else b"{}"
        try:
            return json.loads(raw or b"{}")
        except Exception:
            return {}

    def _cookie(self) -> str | None:
        header = self.headers.get("Cookie", "")
        for part in header.split(";"):
            k, _, v = part.strip().partition("=")
            if k == "session":
                return v or None
        return None

    def _current_user(self) -> str | None:
        return _session_user(self._cookie())

    def _json_with_cookie(self, obj, cookie: str | None, status: int = 200) -> None:
        body = json.dumps(obj, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(INDEX.read_bytes(), "text/html; charset=utf-8")
        elif path in ("/user", "/user.html"):
            self._send(USER_INDEX.read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/me":
            user_id = self._current_user()
            if not user_id:
                self._json({"user": None}, 401)
            else:
                prof = _load_profile(user_id) or {}
                self._json({"user": {"id": user_id, "name": prof.get("name", user_id)}})
        elif path == "/api/tasks":
            self._json(build_tasks())
        elif path == "/api/userdata-schema":
            self._json(_userdata_schema())
        elif path == "/api/runs":
            self._json(_runs_json())
        elif path == "/api/stats":
            self._json(_stats_json())
        elif path == "/api/optimize/jobs":
            self._json(_jobs())
        elif path == "/api/optimize/status":
            job_id = (parse_qs(urlparse(self.path).query).get("job_id") or [""])[0]
            self._json(_job_status(job_id))
        elif path.startswith("/api/task/"):
            name = unquote(path[len("/api/task/"):])
            try:
                self._json(_task_detail(_load_capability(name)))
            except Exception as e:
                self._json({"error": str(e)}, 404)
        elif path.startswith("/api/screenshots/"):
            rel = unquote(path[len("/api/screenshots/"):])
            fp = (SHOTS / rel).resolve()
            if str(fp).startswith(str(SHOTS.resolve())) and fp.is_file():
                body = fp.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(body)))
                # Screenshots are overwritten per run (fixed filename); forbid
                # caching so a re-run shows the fresh image, not a stale one.
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
                self.end_headers()
                self.wfile.write(body)
            else:
                self._json({"error": "not found"}, 404)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            data = self._read_json()
            if path == "/api/login":
                username = (data.get("username") or "").strip()
                password = data.get("password") or ""
                user_id = _authenticate(username, password)
                if not user_id:
                    self._json_with_cookie({"ok": False, "error": "invalid credentials"},
                                           None, 401)
                    return
                token = _new_session(user_id)
                prof = _load_profile(user_id) or {}
                self._json_with_cookie(
                    {"ok": True, "user": {"id": user_id, "name": prof.get("name", user_id)}},
                    f"session={token}; Path=/; HttpOnly")
                return
            if path == "/api/logout":
                _drop_session(self._cookie())
                self._json_with_cookie({"ok": True},
                                       "session=; Path=/; Max-Age=0")
                return
            if path == "/api/run":
                name = data.get("task")
                inputs = data.get("inputs") or {}
                source = data.get("source", "user")
                expect = data.get("expect") or None
                if source not in ("user", "admin"):
                    source = "user"
                if not name:
                    self._json({"error": "task required"}, 400)
                    return
                user_id = self._current_user()
                self._json(_run_task(name, inputs, source=source, expect=expect,
                                     user_id=user_id))
            elif path == "/api/rerun":
                run_id = data.get("run_id")
                if not run_id:
                    self._json({"error": "run_id required"}, 400)
                    return
                self._json(_rerun_task(run_id))
            elif path == "/api/optimize":
                name = data.get("task")
                instruction = (data.get("instruction") or "").strip()
                if not name or not instruction:
                    self._json({"error": "task and instruction required"}, 400)
                    return
                self._json(_optimize_async(name, instruction))
            elif path == "/api/discover":
                self._json(_discover_async(
                    data.get("url", ""), data.get("task", ""), data.get("name", "")
                ))
            elif path == "/api/examples":
                self._json(_edit_examples(
                    data.get("task", ""),
                    data.get("action", "add"),
                    int(data.get("index", -1)),
                    data.get("example") or {},
                ))
            elif path == "/api/bind":
                self._json(_set_bind(
                    data.get("task", ""), data.get("input", ""), data.get("bind", "")
                ))
            elif path == "/api/rename":
                self._json(_rename_task(
                    data.get("task", ""), data.get("new_name", "")
                ))
            else:
                self._json({"error": "not found"}, 404)
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/runs":
            self._json(_reset_runs())
        elif path.startswith("/api/task/"):
            name = unquote(path[len("/api/task/"):])
            try:
                self._json(_delete_task(name))
            except Exception as e:
                self._json({"error": str(e)}, 500)
        else:
            self._json({"error": "not found"}, 404)

    def log_message(self, format, *args) -> None:
        pass


class _ThreadingHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer that skips HTTPServer's reverse-DNS lookup.

    Python's http.server.HTTPServer.server_bind() calls socket.getfqdn(host),
    which on macOS does a reverse-DNS (mDNS) lookup that can hang indefinitely
    when mDNSResponder doesn't answer — leaving the socket in CLOSED (never
    LISTEN). server_name is only used for logging (which we suppress), so set it
    to the raw host string instead of resolving it.
    """
    def server_bind(self):
        import socketserver
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = port


def main() -> None:
    print(f"task dashboard on http://localhost:{PORT}  (LAN: http://<this-mac-ip>:{PORT})")
    _ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
