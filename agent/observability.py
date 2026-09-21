"""Observability & repair loop (DESIGN §7).

Deterministic replay is cheap enough to run constantly, so every run is
*recorded*, *measured*, and can be *replayed* to reproduce a failure. This
module is the persistence + telemetry layer behind the failure inbox and the
monitor -> diagnose -> fix -> re-verify loop.

Persistence is append-only: each ReplayRun is one JSON file under
`evidence/runs/`. Customer-entered input values are AES-256-GCM encrypted at
rest before they touch disk (DESIGN §9) — the store decrypts them only when a
case is loaded for replay.
"""
import json
from pathlib import Path
from typing import Optional

from .replay import ReplayRun, replay

EVIDENCE_DIR = Path("evidence")
RUNS_DIR = EVIDENCE_DIR / "runs"
ARTIFACT_DIR = Path("artifacts")
MOCK_URL = "http://localhost:9000/"


class ReplayStore:
    """Append-only store of ReplayRun records, one JSON file per run."""

    def __init__(self, directory: Path = RUNS_DIR, tenant_id: str = "default"):
        self.directory = directory
        self.tenant_id = tenant_id
        # resolved state lives alongside the runs dir (so a custom directory in
        # tests doesn't leak into the real evidence/ tree).
        self.resolved_path = directory.parent / "resolved.json"

    # ---- persistence ----

    def record(self, run: ReplayRun) -> str:
        """Persist a ReplayRun. Inputs are encrypted at rest. Successful runs
        store no inputs/outputs — we only care that they succeeded and how long
        they took, so their data would be redundant. Failed runs keep their
        encrypted inputs so a maintainer can replay the exact failing invocation.
        Returns the run id (the filename stem)."""
        from . import crypto

        if run.result == "failure":
            key = crypto.get_key(self.tenant_id)
            encrypted_inputs = {
                k: crypto.encrypt(str(v), key) for k, v in run.inputs.items()
            }
            stored = run.model_copy(update={"inputs": encrypted_inputs, "outputs": {}})
        else:
            stored = run.model_copy(update={"inputs": {}, "outputs": {}})

        self.directory.mkdir(parents=True, exist_ok=True)
        run_id = (
            f"{run.capability}__"
            f"{run.started_at.strftime('%Y%m%dT%H%M%S%f')}__{run.result}"
        )
        (self.directory / f"{run_id}.json").write_text(stored.model_dump_json(indent=2))
        return run_id

    def load(self, run_id: str, decrypt: bool = True) -> ReplayRun:
        """Read a run back. Inputs are decrypted by default (replay needs them
        plaintext); pass decrypt=False for telemetry/inbox reads that only
        inspect the result and diagnostic."""
        run = ReplayRun.model_validate_json((self.directory / f"{run_id}.json").read_text())
        if decrypt and run.inputs:
            from . import crypto

            key = crypto.get_key(self.tenant_id)
            run.inputs = {
                k: crypto.decrypt(v, key) if crypto.is_encrypted(v) else v
                for k, v in run.inputs.items()
            }
        return run

    def list_runs(self) -> list[ReplayRun]:
        """Every recorded run, oldest first. Inputs left encrypted (cheap read)."""
        if not self.directory.exists():
            return []
        runs = []
        for f in sorted(self.directory.glob("*.json")):
            try:
                runs.append(ReplayRun.model_validate_json(f.read_text()))
            except Exception:
                continue
        return runs

    # ---- telemetry ----

    def telemetry(self) -> list[dict]:
        """Success / business-outcome / failure rates per capability+version."""
        stats: dict[tuple, dict] = {}
        for run in self.list_runs():
            key = (run.capability, run.version)
            s = stats.setdefault(key, {
                "capability": run.capability,
                "version": run.version,
                "success": 0,
                "business_outcome": 0,
                "failure": 0,
                "total": 0,
            })
            s[run.result] += 1
            s["total"] += 1
        out = []
        for s in stats.values():
            s = dict(s)
            # "succeeded" = any call that returned JSON (success + business_outcome);
            # only failure (mid-run error-out) counts against the success rate.
            s["succeeded"] = s["success"] + s["business_outcome"]
            s["success_rate"] = round(s["succeeded"] / s["total"], 3)
            s["business_rate"] = round(s["business_outcome"] / s["total"], 3)
            s["failure_rate"] = round(s["failure"] / s["total"], 3)
            out.append(s)
        return sorted(out, key=lambda s: (s["capability"], s["version"]))

    # ---- failure inbox ----

    def failures(self, unresolved_only: bool = True) -> list[tuple[str, ReplayRun]]:
        """The failure inbox: every non-success run, with its run id."""
        resolved = self._resolved_set()
        out = []
        for f in sorted(self.directory.glob("*.json")) if self.directory.exists() else []:
            run = ReplayRun.model_validate_json(f.read_text())
            if run.result != "failure":
                continue
            if unresolved_only and f.stem in resolved:
                continue
            out.append((f.stem, run))
        return out

    # ---- repair state ----

    def _resolved_set(self) -> set[str]:
        if not self.resolved_path.exists():
            return set()
        try:
            return set(json.loads(self.resolved_path.read_text()))
        except Exception:
            return set()

    def mark_resolved(self, run_id: str, resolved: bool = True) -> None:
        current = self._resolved_set()
        if resolved:
            current.add(run_id)
        else:
            current.discard(run_id)
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        self.resolved_path.write_text(json.dumps(sorted(current), indent=2))

    def is_resolved(self, run_id: str) -> bool:
        return run_id in self._resolved_set()


def replay_case(run: ReplayRun, screenshot_dir: Optional[str] = None,
                policy=None, handoff=None) -> ReplayRun:
    """Replay-the-error (DESIGN §7): re-run the exact capability + inputs that
    produced a failure, so a maintainer can reproduce it deterministically and
    verify a fix against the original inputs."""
    from playwright.sync_api import sync_playwright

    from .artifact import Capability

    cap = Capability.model_validate_json(
        (ARTIFACT_DIR / f"{run.capability}.json").read_text()
    )
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(MOCK_URL)
        new_run = replay(page, cap, inputs=run.inputs,
                         screenshot_dir=screenshot_dir, policy=policy, handoff=handoff)
        browser.close()
    return new_run
