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
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.observability import ARTIFACT_DIR, ReplayStore  # noqa: E402

INDEX = ROOT / "dashboard" / "index.html"
SHOTS = ROOT / "dashboard" / "screenshots"
PORT = 8123
MOCK_URL = "http://localhost:9000/"

# The mock pages a deactivate_member replay path can touch, in order.
PAGE_SHOTS = [
    ("search", "Search page"),
    ("detail", "Member detail"),
    ("confirm", "Deactivation confirm"),
    ("success", "Result — success"),
    ("no_such_member", "Result — no such member"),
    ("access_denied", "Result — access denied"),
]


def _load_capability(name: str):
    from agent.artifact import Capability
    return Capability.model_validate_json((ARTIFACT_DIR / f"{name}.json").read_text())


def _capabilities() -> list[dict]:
    if not ARTIFACT_DIR.exists():
        return []
    out = []
    for f in sorted(ARTIFACT_DIR.glob("*.json")):
        try:
            out.append(_load_capability(f.stem))
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
            for pid, label in PAGE_SHOTS if (SHOTS / f"{pid}.png").exists()
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
        page.goto(MOCK_URL)
        run = replay(page, cap, inputs=inputs, screenshot_dir=str(SHOTS))
        page.screenshot(path=str(SHOTS / shot))
        browser.close()
    return {
        "task": name,
        "result": run.result,
        "diagnostic": run.diagnostic,
        "screenshot": f"/api/screenshots/{shot}",
        "inputs": inputs,
        "duration_ms": run.duration_ms,
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
            else:
                self._json({"error": "not found"}, 404)
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def log_message(self, format, *args) -> None:
        pass


def main() -> None:
    print(f"task dashboard on http://localhost:{PORT}  (LAN: http://<this-mac-ip>:{PORT})")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
