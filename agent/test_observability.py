"""Phase 6 end-to-end: ReplayStore persistence, inputs-at-rest encryption,
telemetry aggregation, failure inbox, and the resolve loop.

Runs without the mock / any LLM — it drives the store directly with
constructed ReplayRun records, so it's fast and deterministic.
"""
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from agent.observability import ReplayStore
from agent.replay import ReplayRun


def make_run(result, inputs=None, diagnostic=""):
    return ReplayRun(
        capability="deactivate_member",
        version="1.0.0",
        inputs=inputs or {},
        result=result,
        outputs={},
        diagnostic=diagnostic,
        steps=[],
        started_at=datetime.now(timezone.utc),
        duration_ms=10,
    )


with tempfile.TemporaryDirectory() as td:
    store = ReplayStore(directory=Path(td) / "runs")

    # 1. record three runs — one per result state.
    id_ok = store.record(make_run("success", inputs={"member_id": "1001"}))
    id_bo = store.record(make_run("business_outcome", inputs={"member_id": "9999"}))
    id_fail = store.record(make_run("failure", inputs={"member_id": "1002"},
                                    diagnostic="step 3 failed: ACCESS DENIED"))

    # 2. plaintext inputs must not leak to disk (encrypted at rest, DESIGN §9).
    raw = (Path(td) / "runs" / f"{id_ok}.json").read_text()
    assert "1001" not in raw, "plaintext input leaked to disk"
    print("1. record: inputs encrypted at rest on disk")

    # 3. load round-trips and decrypts inputs.
    loaded = store.load(id_ok)
    assert loaded.inputs == {"member_id": "1001"}
    assert loaded.result == "success"
    print("2. load: round-trips + decrypts inputs")

    # 4. telemetry aggregates all three states.
    stats = store.telemetry()
    assert len(stats) == 1, "one capability should yield one telemetry row"
    s = stats[0]
    assert s["success"] == 1 and s["business_outcome"] == 1 and s["failure"] == 1
    assert s["total"] == 3
    assert abs(s["success_rate"] - 1 / 3) < 0.001
    print(f"3. telemetry: success={s['success']} business={s['business_outcome']} "
          f"failure={s['failure']} success_rate={s['success_rate']}")

    # 5. failure inbox surfaces only the failure.
    fails = store.failures()
    assert len(fails) == 1
    assert fails[0][0] == id_fail
    assert "ACCESS DENIED" in fails[0][1].diagnostic
    print("4. failure inbox: exactly one unresolved failure")

    # 6. resolve closes the loop; inbox empties.
    store.mark_resolved(id_fail)
    assert store.is_resolved(id_fail)
    assert store.failures() == []
    print("5. resolve: marked resolved, inbox now empty")

print("\nOK — Phase 6: ReplayStore, encryption-at-rest, telemetry, inbox, "
      "resolve loop all verified")
