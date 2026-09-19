"""Add the lookup_member capability — look up a member and view their detail.

This is a second capability (task) for the dashboard, complementing
deactivate_member. It records ONLY the "search -> view detail" flow, stopping
before deactivation, to show that one target system yields several distinct,
replayable tasks.

Run from repo root (mock must be running on localhost:9000):

    .venv/bin/python scripts/add_lookup_member.py
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

TASK = (
    "Look up member 1001 and view their account detail. "
    "Stop as soon as the member detail page is displayed — do NOT deactivate the account."
)

NAME = "lookup_member"
MOCK = "http://localhost:9000/"


def _replay(cap, inputs):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(MOCK)
        run = replay(page, cap, inputs=inputs)
        browser.close()
    return run


def main() -> int:
    # 1. discovery (the LLM appears here, and only here)
    print("== discovery ==")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(MOCK)
        discovery = run_discovery(page, TASK)
        browser.close()
    print(f"  status={discovery['status']}  steps={len(discovery['steps'])}")

    # 2. artifact
    cap = serialize(
        discovery,
        name=NAME,
        description="Look up a member by ID and view their account detail.",
        inputs=[{
            "name": "member_id",
            "type": "str",
            "required": True,
            "description": "Member ID to look up",
        }],
        outputs=[
            {"name": "name", "type": "str", "source": "member detail page", "label": "NAME"},
            {"name": "status", "type": "str", "source": "member detail page", "label": "STATUS"},
            {"name": "balance", "type": "str", "source": "member detail page", "label": "BALANCE"},
        ],
        checkpoint="member detail page is displayed",
        checkpoint_text="MEMBER DETAIL",
        business_outcomes=[{"text": "NO SUCH MEMBER", "label": "member not found"}],
        failure_patterns=[],
        value_params={"1001": "member_id"},
        domain="localhost:9000",
    )

    ARTIFACT_DIR.mkdir(exist_ok=True)
    EVIDENCE_DIR.mkdir(exist_ok=True)
    (ARTIFACT_DIR / f"{NAME}.json").write_text(cap.model_dump_json(indent=2))
    (EVIDENCE_DIR / f"artifact_{NAME}.json").write_text(cap.model_dump_json(indent=2))
    (EVIDENCE_DIR / f"discovery_{NAME}.json").write_text(
        json.dumps(discovery, indent=2, default=str)
    )
    print(f"artifact saved: {len(cap.steps)} steps, checkpoint_text={cap.checkpoint_text!r}")

    # 3. replay three scenarios -> three ReplayRun records
    print("== replay ==")
    store = ReplayStore()
    for inputs in ({"member_id": "1001"}, {"member_id": "9999"}, {"member_id": "1002"}):
        run = _replay(cap, inputs)
        run_id = store.record(run)
        print(f"  {run_id}  result={run.result}  inputs={inputs}  {run.diagnostic or ''}")

    # 4. telemetry
    print("\n== telemetry ==")
    for s in store.telemetry():
        print(f"  {s['capability']} v{s['version']}: total={s['total']} "
              f"success={s['success']} business={s['business_outcome']} "
              f"failure={s['failure']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
