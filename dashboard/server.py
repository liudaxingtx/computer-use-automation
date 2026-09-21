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

from agent.observability import ARTIFACT_DIR, EVIDENCE_DIR, RUNS_DIR, ReplayStore  # noqa: E402

INDEX = ROOT / "dashboard" / "index.html"
USER_INDEX = ROOT / "dashboard" / "user.html"
SHOTS = ROOT / "dashboard" / "screenshots"
PORT = 8123
MOCK_URL = "http://localhost:9000/"

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
    stats = {"success": 0, "business_outcome": 0, "failure": 0, "total": 0}
    for run in ReplayStore().list_runs():
        if run.capability == name:
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
                    source: Literal["user", "admin"] = "user") -> dict:
    from playwright.sync_api import sync_playwright

    from agent.replay import replay

    shot = f"run_{name}.png"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(cap.start_url or MOCK_URL)
        run = replay(page, cap, inputs=inputs, screenshot_dir=str(SHOTS))
        page.screenshot(path=str(SHOTS / shot))
        browser.close()
    run.source = source
    # Persist every invocation so the statistics report can answer "how many
    # times was this task really used, and what happened".
    ReplayStore().record(run)
    return {
        "task": name,
        "result": run.result,
        "diagnostic": run.diagnostic,
        "outputs": run.outputs,
        "screenshot": f"/api/screenshots/{shot}?t={int(time.time() * 1000)}",
        "steps": run.steps,
        "inputs": inputs if echo_inputs else None,
        "duration_ms": run.duration_ms,
    }


def _run_task(name: str, inputs: dict, source: Literal["user", "admin"] = "user") -> dict:
    cap = _load_capability(name)
    return _execute_replay(cap, inputs, name, source=source)


def _rerun_task(run_id: str) -> dict:
    """Re-run a recorded invocation with its original inputs. Inputs are
    decrypted only at the moment of replay (never exposed by the report); the
    re-run is itself recorded as a new invocation (an admin op, not a user call)."""
    store = ReplayStore()
    run = store.load(run_id, decrypt=True)
    cap = _load_capability(run.capability)
    result = _execute_replay(cap, run.inputs, run.capability,
                             echo_inputs=False, source="admin")
    result["rerun_of"] = run_id
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
        "- checkpoint_text must be literal text that appears on the page on success.\n"
        "- business_outcomes are legitimate expected answers (e.g. \"no such member\"); "
        "failure_patterns are hard errors (e.g. \"access denied\").\n"
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
    from datetime import datetime, timezone
    new_cap.meta.optimized_at = datetime.now(timezone.utc)
    (ARTIFACT_DIR / f"{name}.json").write_text(new_cap.model_dump_json(indent=2))

    return {
        "ok": True,
        "name": name,
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

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(INDEX.read_bytes(), "text/html; charset=utf-8")
        elif path in ("/user", "/user.html"):
            self._send(USER_INDEX.read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/tasks":
            self._json(build_tasks())
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
            if path == "/api/run":
                name = data.get("task")
                inputs = data.get("inputs") or {}
                source = data.get("source", "user")
                if source not in ("user", "admin"):
                    source = "user"
                if not name:
                    self._json({"error": "task required"}, 400)
                    return
                self._json(_run_task(name, inputs, source=source))
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


def main() -> None:
    print(f"task dashboard on http://localhost:{PORT}  (LAN: http://<this-mac-ip>:{PORT})")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
