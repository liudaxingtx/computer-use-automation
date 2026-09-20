"""Patch the auto-recorded register/login capabilities with their error-state
signals.

Auto-discovery only sees the happy path, so business_outcomes and
failure_patterns are filled in here (DESIGN §5: error handling is first-class).

  register_operator:
    business:  USERNAME TAKEN  (a legitimate answer, not a crash)
    failure:   INVALID PASSWORD (too short), INVALID INPUT (missing field)

  login_operator:
    business:  INVALID CREDENTIALS (wrong username or password)

Run from repo root:  .venv/bin/python scripts/patch_outcomes.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.artifact import Capability, OutcomePattern
from agent.observability import ARTIFACT_DIR, EVIDENCE_DIR


def _save(cap: Capability) -> None:
    (ARTIFACT_DIR / f"{cap.meta.name}.json").write_text(cap.model_dump_json(indent=2))
    (EVIDENCE_DIR / f"artifact_{cap.meta.name}.json").write_text(cap.model_dump_json(indent=2))
    print(f"patched {cap.meta.name}: business={[b.text for b in cap.business_outcomes]} "
          f"failure={[f.text for f in cap.failure_patterns]}")


def main() -> int:
    reg = Capability.model_validate_json((ARTIFACT_DIR / "register_operator.json").read_text())
    reg.business_outcomes = [OutcomePattern(text="USERNAME TAKEN", label="username already registered")]
    reg.failure_patterns = [
        OutcomePattern(text="INVALID PASSWORD", label="password too short"),
        OutcomePattern(text="INVALID INPUT", label="missing required field"),
    ]
    _save(reg)

    login = Capability.model_validate_json((ARTIFACT_DIR / "login_operator.json").read_text())
    login.business_outcomes = [OutcomePattern(text="INVALID CREDENTIALS", label="wrong username or password")]
    _save(login)
    return 0


if __name__ == "__main__":
    sys.exit(main())
