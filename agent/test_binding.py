"""Per-user input binding — auto-fetching task inputs from the calling user's
private data (e.g. member_id <- userBO.eeID) at replay time.

Covers four layers:
  1. schema    — InputSpec.bind serializes and defaults to "".
  2. resolve   — dotted-path resolution against users/<id>/profile.json.
  3. bind      — _bind_user_data injects / overrides / isolates / reports.
  4. e2e       — a real browser replay: alice and bob run lookup_member and get
                 *their own* member (the caller-supplied value is overridden).

Run from the repo root with the mock app up:
    .venv/bin/python -m agent.test_binding
"""
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Seed the demo users so userBO.eeID / userBO.department exist.
subprocess.run([sys.executable, str(ROOT / "scripts" / "init_users.py")],
               check=True, capture_output=True)

from agent.artifact import Capability, CapabilityMeta, InputSpec, Step  # noqa: E402
from dashboard.server import (  # noqa: E402
    MOCK_URL, _bind_user_data, _load_capability, _resolve_user_data,
)


def test_schema_roundtrip() -> None:
    spec = InputSpec(name="member_id", type="str", required=True,
                     description="member to look up", bind="userBO.eeID")
    d = spec.model_dump()
    assert d["bind"] == "userBO.eeID", d
    assert InputSpec(name="member_id").bind == ""
    print("OK — InputSpec.bind serializes + defaults to empty")


def test_resolve_user_data() -> None:
    # Seeded users: alice eeID=1001, bob eeID=1002 (see scripts/init_users.py).
    assert _resolve_user_data("alice", "userBO.eeID") == "1001"
    assert _resolve_user_data("bob", "userBO.eeID") == "1002"
    assert _resolve_user_data("alice", "userBO.department") == "Member Services"
    assert _resolve_user_data("alice", "userBO.nonexistent") is None
    assert _resolve_user_data("alice", "no_such_namespace.x") is None
    assert _resolve_user_data(None, "userBO.eeID") is None
    assert _resolve_user_data("ghost", "userBO.eeID") is None
    print("OK — dotted-path resolution walks userBO + handles missing/absent")


def _cap(bind: str) -> Capability:
    return Capability(
        meta=CapabilityMeta(name="t"), start_url="",
        inputs=[InputSpec(name="member_id", bind=bind)],
        steps=[],
    )


def test_bind_user_data() -> None:
    # bound input is injected from the user's folder
    inputs, missing = _bind_user_data(_cap("userBO.eeID"), {}, "alice")
    assert inputs == {"member_id": "1001"}, inputs
    assert missing == []

    # spoof prevention: a caller-supplied value is ALWAYS overridden
    inputs, missing = _bind_user_data(_cap("userBO.eeID"), {"member_id": "9999"}, "alice")
    assert inputs == {"member_id": "1001"}, inputs

    # per-user isolation: different users resolve to different values
    inputs, _ = _bind_user_data(_cap("userBO.eeID"), {}, "bob")
    assert inputs == {"member_id": "1002"}, inputs

    # no user (admin op) → untouched
    inputs, missing = _bind_user_data(_cap("userBO.eeID"), {"member_id": "1001"}, None)
    assert inputs == {"member_id": "1001"} and missing == []

    # missing path → reported, not silently skipped
    _, missing = _bind_user_data(_cap("userBO.nope"), {}, "alice")
    assert missing and "member_id" in missing[0], missing

    # unbound input → untouched
    inputs, missing = _bind_user_data(_cap(""), {"member_id": "1003"}, "alice")
    assert inputs == {"member_id": "1003"} and missing == []
    print("OK — bound inputs auto-fetch, override the caller, isolate per user")


def test_e2e_binding() -> None:
    try:
        urllib.request.urlopen(MOCK_URL, timeout=2)
    except Exception:
        print("SKIP — mock app not running (start: python3 mock-app/server.py)")
        return

    from playwright.sync_api import sync_playwright

    from agent.replay import replay

    cap = _load_capability("lookup_member")
    cap.inputs[0].bind = "userBO.eeID"   # bind in-memory; no artifact mutation

    def run_as(user_id: str):
        # the caller tries to spoof member_id=9999 — must be overridden
        inputs, missing = _bind_user_data(cap, {"member_id": "9999"}, user_id)
        assert not missing, missing
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            page = b.new_page()
            page.goto(cap.start_url or MOCK_URL)
            run = replay(page, cap, inputs=inputs)
            b.close()
        return run

    alice = run_as("alice")
    assert alice.result == "success", alice.diagnostic
    assert alice.outputs.get("name") == "JOHN SMITH", alice.outputs

    bob = run_as("bob")
    assert bob.result == "success", bob.diagnostic
    assert bob.outputs.get("name") == "JANE DOE", bob.outputs

    print("OK — e2e: alice→JOHN SMITH (1001), bob→JANE DOE (1002); "
          "caller-supplied 9999 was overridden")


if __name__ == "__main__":
    test_schema_roundtrip()
    test_resolve_user_data()
    test_bind_user_data()
    test_e2e_binding()
    print("ALL BINDING TESTS PASSED")
