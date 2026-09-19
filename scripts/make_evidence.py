"""Generate Phase 6 evidence: a saved artifact, a real discovery log, and
replay logs — including one that hits an error state (DESIGN §7).

Produces, under `evidence/`:
    artifact_deactivate_member.json    — the distilled Capability (also copied to artifacts/)
    discovery_deactivate_member.json   — the raw discovery transcript
    runs/<...>__success.json           — replay log: happy path
    runs/<...>__business_outcome.json  — replay log: "no such member"
    runs/<...>__failure.json           — replay log: "access denied" (the error case)
    screenshots/<...>_failure.png      — screenshot captured on the failure

Run from repo root:  .venv/bin/python scripts/make_evidence.py
(requires the mock app running on localhost:9000)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright

from agent.artifact import serialize
from agent.loop import run_discovery
from agent.observability import ARTIFACT_DIR, EVIDENCE_DIR, ReplayStore
from agent.replay import replay

TASK = "Search for member 1001, view their detail, then deactivate the account."


def _replay(cap, inputs, screenshot_dir):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("http://localhost:9000/")
        run = replay(page, cap, inputs=inputs, screenshot_dir=screenshot_dir)
        browser.close()
    return run


def main() -> int:
    # 1. discovery (the LLM appears here, and only here)
    print("== discovery ==")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("http://localhost:9000/")
        discovery = run_discovery(page, TASK)
        browser.close()

    # 2. artifact
    cap = serialize(
        discovery,
        name="deactivate_member",
        description="Look up a member by ID and deactivate their account.",
        inputs=[{"name": "member_id", "type": "str", "required": True}],
        outputs=[{"name": "status", "type": "str", "source": "result page"}],
        checkpoint="result page shows SUCCESS",
        checkpoint_text="SUCCESS",
        business_outcomes=[{"text": "NO SUCH MEMBER", "label": "member not found"}],
        failure_patterns=[{"text": "ACCESS DENIED", "label": "permission denied"}],
        value_params={"1001": "member_id"},
    )

    ARTIFACT_DIR.mkdir(exist_ok=True)
    (ARTIFACT_DIR / "deactivate_member.json").write_text(cap.model_dump_json(indent=2))
    EVIDENCE_DIR.mkdir(exist_ok=True)
    (EVIDENCE_DIR / "artifact_deactivate_member.json").write_text(cap.model_dump_json(indent=2))
    (EVIDENCE_DIR / "discovery_deactivate_member.json").write_text(
        json.dumps(discovery, indent=2, default=str)
    )
    print(f"artifact saved: {len(cap.steps)} steps, checkpoint_text={cap.checkpoint_text!r}")

    # 3. replay the three scenarios -> three ReplayRun records (one hits an error)
    print("== replay ==")
    store = ReplayStore()
    shot_dir = str(EVIDENCE_DIR / "screenshots")
    for inputs in ({"member_id": "1001"}, {"member_id": "9999"}, {"member_id": "1002"}):
        run = _replay(cap, inputs, shot_dir)
        run_id = store.record(run)
        print(f"  {run_id}")
        print(f"    result={run.result}  {run.diagnostic or ''}")

    # 4. show the observability surface working
    print("\n== telemetry ==")
    for s in store.telemetry():
        print(f"  {s['capability']} v{s['version']}: total={s['total']} "
              f"success={s['success']} business={s['business_outcome']} "
              f"failure={s['failure']} success_rate={s['success_rate']}")

    print("\n== failure inbox ==")
    fails = store.failures()
    if not fails:
        print("  (none)")
    for run_id, run in fails:
        print(f"  {run_id}: {run.diagnostic[:90]}")

    print("\ndone. evidence/ tree:")
    for f in sorted(EVIDENCE_DIR.rglob("*")):
        if f.is_file():
            print(f"  {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
