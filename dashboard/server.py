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
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.observability import ARTIFACT_DIR, EVIDENCE_DIR, RUNS_DIR, ReplayStore  # noqa: E402

INDEX = ROOT / "dashboard" / "index.html"
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
    # Prefer the runtime artifact store (artifacts/); fall back to the committed
    # evidence seed (evidence/artifact_<name>.json) so a fresh clone — where
    # artifacts/ is git-ignored and absent — still shows every pre-recorded task.
    p = ARTIFACT_DIR / f"{name}.json"
    if not p.exists():
        p = EVIDENCE_DIR / f"artifact_{name}.json"
    return Capability.model_validate_json(p.read_text())


def _capabilities() -> list[dict]:
    # Union of the runtime store (takes precedence) and the committed evidence
    # seed, so the dashboard is complete both in a working tree and after a
    # fresh clone.
    names: set[str] = set()
    if ARTIFACT_DIR.exists():
        names.update(f.stem for f in ARTIFACT_DIR.glob("*.json"))
    if EVIDENCE_DIR.exists():
        for f in EVIDENCE_DIR.glob("artifact_*.json"):
            names.add(f.stem[len("artifact_"):])
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


def _task_summary(cap) -> dict:
    stats = _run_stats(cap.meta.name)
    return {
        "name": cap.meta.name,
        "version": cap.meta.version,
        "description": cap.meta.description,
        "domain": cap.meta.domain,
        "steps_count": len(cap.steps),
        "inputs": [i.model_dump() for i in cap.inputs],
        "verified": stats["success"] > 0,
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
        "steps": steps,
        "screenshots": [
            {"id": pid, "label": label, "url": f"/api/screenshots/{pid}.png"}
            for pid, label in SHOT_MAP.get(cap.meta.name, [])
            if (SHOTS / f"{pid}.png").exists()
        ],
        "runs": _run_stats(cap.meta.name),
    }


def _run_task(name: str, inputs: dict) -> dict:
    from playwright.sync_api import sync_playwright

    from agent.replay import replay

    cap = _load_capability(name)
    shot = f"run_{name}.png"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(cap.start_url or MOCK_URL)
        run = replay(page, cap, inputs=inputs, screenshot_dir=str(SHOTS))
        page.screenshot(path=str(SHOTS / shot))
        browser.close()
    # Persist every invocation so the call-log / monitoring view can answer
    # "how many times was this task really used, and what happened".
    ReplayStore().record(run)
    return {
        "task": name,
        "result": run.result,
        "diagnostic": run.diagnostic,
        "outputs": run.outputs,
        "screenshot": f"/api/screenshots/{shot}",
        "inputs": inputs,
        "duration_ms": run.duration_ms,
    }


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


def _delete_task(name: str) -> dict:
    """Delete a recorded task and everything tied to it: the artifact, its
    evidence copies, the discovery log, and every recorded run (call history).
    Returns what was removed so the UI can confirm the result."""
    if not (ARTIFACT_DIR / f"{name}.json").exists():
        return {"ok": False, "error": f"task '{name}' not found"}

    removed = []
    for p in (ARTIFACT_DIR / f"{name}.json",
              EVIDENCE_DIR / f"artifact_{name}.json",
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


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:40]


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

    # persist artifact + evidence
    ARTIFACT_DIR.mkdir(exist_ok=True)
    EVIDENCE_DIR.mkdir(exist_ok=True)
    (ARTIFACT_DIR / f"{name}.json").write_text(cap.model_dump_json(indent=2))
    (EVIDENCE_DIR / f"artifact_{name}.json").write_text(cap.model_dump_json(indent=2))
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
    }


def build_tasks() -> dict:
    caps = _capabilities()
    tasks = [_task_summary(c) for c in caps]
    return {
        "tasks": tasks,
        "summary": {
            "total": len(tasks),
            "verified": sum(1 for t in tasks if t["verified"]),
            "unverified": sum(1 for t in tasks if not t["verified"]),
        },
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, ctype: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
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
        elif path == "/api/tasks":
            self._json(build_tasks())
        elif path == "/api/runs":
            self._json(_runs_json())
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
                self._send(fp.read_bytes(), "image/png")
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
                if not name:
                    self._json({"error": "task required"}, 400)
                    return
                self._json(_run_task(name, inputs))
            elif path == "/api/discover":
                self._json(_discover_task(
                    data.get("url", ""), data.get("task", ""), data.get("name", "")
                ))
            else:
                self._json({"error": "not found"}, 404)
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/api/task/"):
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
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
