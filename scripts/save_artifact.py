"""Generate the deactivate_member Capability and save it to artifacts/ for the CLI.

Run from the repo root:  .venv/bin/python scripts/save_artifact.py
"""
import sys
from pathlib import Path

# make the repo root importable when run as `python scripts/save_artifact.py`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright

from agent.artifact import serialize
from agent.loop import run_discovery

TASK = "Search for member 1001, view their detail, then deactivate the account."

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto("http://localhost:9000/")
    discovery = run_discovery(page, TASK)
    browser.close()

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

Path("artifacts").mkdir(exist_ok=True)
Path("artifacts/deactivate_member.json").write_text(cap.model_dump_json(indent=2))
print("saved artifacts/deactivate_member.json "
      f"({len(cap.steps)} steps, checkpoint_text={cap.checkpoint_text!r})")
