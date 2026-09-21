"""Enrich every capability's input specs with a human-meaningful `description`
and a more useful `type` (password fields become `password`, so the user-facing
runner can mask them). Discovery only records the happy path, so these were
empty; this is the same honest post-processing pattern as patch_outcomes.py.

Runs idempotently — safe to re-run.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.artifact import Capability  # noqa: E402

# task -> { input_name: (type, description) }
INPUT_META = {
    "lookup_member": {
        "member_id": ("str", "Member account ID to look up — e.g. 1001"),
    },
    "deactivate_member": {
        "member_id": ("str", "Member account ID to deactivate — e.g. 1001"),
    },
    "register_operator": {
        "username": ("str", "Desired login username"),
        "password": ("password", "Password (at least 6 characters)"),
        "email": ("str", "Operator email address"),
    },
    "login_operator": {
        "username": ("str", "Login username"),
        "password": ("password", "Login password"),
    },
    "saucedemo_login": {
        "user-name": ("str", "Store username — e.g. standard_user"),
        "password": ("password", "Store password — e.g. secret_sauce"),
    },
    "saucedemo_checkout": {
        "user-name": ("str", "Store username — e.g. standard_user"),
        "password": ("password", "Store password — e.g. secret_sauce"),
        "firstName": ("str", "Customer first name"),
        "lastName": ("str", "Customer last name"),
        "postalCode": ("str", "Shipping postal / ZIP code"),
    },
    "theinternet_login": {
        "username": ("str", "Login username — e.g. tomsmith"),
        "password": ("password", "Login password"),
    },
}

ARTIFACTS = ROOT / "artifacts"
EVIDENCE = ROOT / "evidence"


def main() -> int:
    for name, meta in INPUT_META.items():
        p = ARTIFACTS / f"{name}.json"
        if not p.exists():
            p = EVIDENCE / f"artifact_{name}.json"
        cap = Capability.model_validate_json(p.read_text())

        changed = 0
        for spec in cap.inputs:
            if spec.name in meta:
                typ, desc = meta[spec.name]
                if spec.type != typ or spec.description != desc:
                    spec.type = typ
                    spec.description = desc
                    changed += 1

        if changed:
            (ARTIFACTS / f"{name}.json").write_text(cap.model_dump_json(indent=2))
            (EVIDENCE / f"artifact_{name}.json").write_text(cap.model_dump_json(indent=2))
            print(f"{name}: enriched {changed} input spec(s)")
        else:
            print(f"{name}: already up to date")

    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
