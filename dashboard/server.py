"""Task dashboard — a dark, glowing management page for the computer-use project.

Serves:
  GET  /                  -> the single-page dashboard
  GET  /api/state         -> aggregated progress + system runtime state
  POST /api/task/toggle   -> flip a task item done/undone
  POST /api/resolve       -> mark a failure case resolved
  POST /api/replay        -> replay-the-error (reproduce a failure)
  GET  /evidence/<path>   -> serve evidence files (failure screenshots)

Run from the repo root:  .venv/bin/python -m dashboard.server
"""
import json
import socket
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.observability import (  # noqa: E402
    ARTIFACT_DIR,
    ReplayStore,
    replay_case,
)

TASKS = ROOT / "dashboard" / "tasks.json"
INDEX = ROOT / "dashboard" / "index.html"
PORT = 8123


def load_tasks() -> dict:
    return json.loads(TASKS.read_text())


def save_tasks(tasks: dict) -> None:
    TASKS.write_text(json.dumps(tasks, indent=2))


def _mock_up() -> bool:
    s = socket.socket()
    s.settimeout(1)
    try:
        s.connect(("127.0.0.1", 9000))
        return True
    except Exception:
        return False
    finally:
        s.close()


def build_state() -> dict:
    tasks = load_tasks()
    store = ReplayStore()

    caps = []
    if ARTIFACT_DIR.exists():
        from agent.artifact import Capability
        for f in sorted(ARTIFACT_DIR.glob("*.json")):
            try:
                c = Capability.model_validate_json(f.read_text())
                caps.append({
                    "name": c.meta.name,
                    "version": c.meta.version,
                    "description": c.meta.description,
                    "steps": len(c.steps),
                })
            except Exception:
                continue

    failures = []
    for run_id, run in store.failures(unresolved_only=False):
        failures.append({
            "run_id": run_id,
            "diagnostic": run.diagnostic,
            "screenshot": run.screenshot,
            "resolved": store.is_resolved(run_id),
        })

    recent = [
        {
            "result": run.result,
            "capability": run.capability,
            "started_at": run.started_at.isoformat(),
            "diagnostic": run.diagnostic,
        }
        for run in store.list_runs()[-8:]
    ][::-1]

    phase_done = sum(1 for p in tasks["phases"] if p["done"])
    sub_done = sum(1 for s in tasks["submission"] if s["done"])

    return {
        "phases": tasks["phases"],
        "submission": tasks["submission"],
        "progress": {
            "phase_done": phase_done,
            "phase_total": len(tasks["phases"]),
            "sub_done": sub_done,
            "sub_total": len(tasks["submission"]),
            "total_done": phase_done + sub_done,
            "total": len(tasks["phases"]) + len(tasks["submission"]),
        },
        "capabilities": caps,
        "telemetry": store.telemetry(),
        "failures": failures,
        "recent": recent,
        "mock_up": _mock_up(),
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
        elif path == "/api/state":
            self._json(build_state())
        elif path.startswith("/evidence/"):
            rel = unquote(path[len("/evidence/"):])
            fp = (ROOT / "evidence" / rel).resolve()
            base = (ROOT / "evidence").resolve()
            if str(fp).startswith(str(base)) and fp.is_file():
                ctype = "image/png" if fp.suffix == ".png" else "application/json"
                self._send(fp.read_bytes(), ctype)
            else:
                self._json({"error": "not found"}, 404)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            data = self._read_json()
            if path == "/api/task/toggle":
                tid = data.get("id")
                tasks = load_tasks()
                for group in ("phases", "submission"):
                    for t in tasks[group]:
                        if t["id"] == tid:
                            t["done"] = not t["done"]
                save_tasks(tasks)
                self._json(build_state())
            elif path == "/api/resolve":
                run_id = data.get("run_id")
                if not run_id:
                    self._json({"error": "run_id required"}, 400)
                    return
                ReplayStore().mark_resolved(run_id)
                self._json(build_state())
            elif path == "/api/replay":
                run_id = data.get("run_id")
                if not run_id:
                    self._json({"error": "run_id required"}, 400)
                    return
                if not _mock_up():
                    self._json({"error": "mock app not running — start it first "
                                          "(python3 mock-app/server.py)"}, 503)
                    return
                store = ReplayStore()
                run = store.load(run_id)
                new_run = replay_case(run, screenshot_dir="evidence/screenshots")
                self._json({"result": new_run.result, "diagnostic": new_run.diagnostic})
            else:
                self._json({"error": "not found"}, 404)
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def log_message(self, format, *args) -> None:
        pass


def main() -> None:
    print(f"task dashboard on http://localhost:{PORT}")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
