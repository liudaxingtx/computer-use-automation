"""Artifact management CLI — the human oversight surface for keeping artifacts
current as target sites drift (design principle #5).

Commands:
    artifact list
    artifact edit <name> --step <n> [--new-name X] [--new-role Y]
    artifact bump <name>
    artifact verify <name> --input key=value ...

Run: .venv/bin/python -m agent.cli list
"""
import argparse
from pathlib import Path

from .artifact import Capability
from .observability import ReplayStore, replay_case

ARTIFACT_DIR = Path("artifacts")


def _path(name: str) -> Path:
    return ARTIFACT_DIR / f"{name}.json"


def _load(name: str) -> Capability:
    return Capability.model_validate_json(_path(name).read_text())


def _save(cap: Capability) -> None:
    ARTIFACT_DIR.mkdir(exist_ok=True)
    _path(cap.meta.name).write_text(cap.model_dump_json(indent=2))


def cmd_list() -> None:
    if not ARTIFACT_DIR.exists() or not list(ARTIFACT_DIR.glob("*.json")):
        print("(no artifacts)")
        return
    for f in sorted(ARTIFACT_DIR.glob("*.json")):
        cap = Capability.model_validate_json(f.read_text())
        print(f"{cap.meta.name:20} v{cap.meta.version}  —  {cap.meta.description} "
              f"({len(cap.steps)} steps)")


def cmd_edit(args) -> None:
    cap = _load(args.name)
    step = cap.steps[args.step - 1]
    if step.target is None:
        print(f"step {args.step} has no locator target")
        return
    if args.new_name is not None:
        step.target.name = args.new_name
    if args.new_role is not None:
        step.target.role = args.new_role
    _save(cap)
    print(f"edited {cap.meta.name} step {args.step}: "
          f"role={step.target.role}, name={step.target.name!r}")


def cmd_bump(args) -> None:
    cap = _load(args.name)
    major, minor, patch = (int(x) for x in cap.meta.version.split("."))
    cap.meta.version = f"{major}.{minor}.{patch + 1}"
    _save(cap)
    print(f"bumped {cap.meta.name} → v{cap.meta.version}")


def cmd_verify(args) -> None:
    cap = _load(args.name)
    inputs = dict(kv.split("=", 1) for kv in args.input)
    from playwright.sync_api import sync_playwright
    from .replay import replay
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("http://localhost:9000/")
        run = replay(page, cap, inputs=inputs)
        browser.close()
    print(f"verify {cap.meta.name} v{cap.meta.version} → {run.result}")
    if run.diagnostic:
        print(f"  diagnostic: {run.diagnostic}")


def cmd_telemetry() -> None:
    """Success / business-outcome / failure rates per capability+version."""
    stats = ReplayStore().telemetry()
    if not stats:
        print("(no replay runs recorded)")
        return
    for s in stats:
        print(f"{s['capability']:20} v{s['version']}  total={s['total']}  "
              f"success={s['success']} business={s['business_outcome']} "
              f"failure={s['failure']}  success_rate={s['success_rate']:.0%}")


def cmd_failures() -> None:
    """The failure inbox: every unresolved non-success run."""
    fails = ReplayStore().failures(unresolved_only=True)
    if not fails:
        print("(failure inbox empty)")
        return
    for run_id, run in fails:
        print(f"{run_id}")
        print(f"    {run.diagnostic[:120]}")


def cmd_replay_case(args) -> None:
    """Replay-the-error: reproduce a failure with its original inputs."""
    store = ReplayStore()
    run = store.load(args.run_id)
    print(f"replaying {args.run_id} ({run.capability} v{run.version}, "
          f"inputs={run.inputs})")
    new_run = replay_case(run, screenshot_dir="evidence/screenshots")
    print(f"  -> {new_run.result}  {new_run.diagnostic or ''}")
    if new_run.result != "failure":
        print("  case now passes — mark it resolved: artifact resolve <id>")


def cmd_resolve(args) -> None:
    ReplayStore().mark_resolved(args.run_id)
    print(f"marked {args.run_id} resolved")


def main() -> None:
    parser = argparse.ArgumentParser(prog="artifact", description="Manage computer-use artifacts.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list artifacts")

    e = sub.add_parser("edit", help="edit a step's locator")
    e.add_argument("name")
    e.add_argument("--step", type=int, required=True, help="1-based step number")
    e.add_argument("--new-name", help="replace the locator name")
    e.add_argument("--new-role", help="replace the locator role")

    b = sub.add_parser("bump", help="bump the patch version")
    b.add_argument("name")

    v = sub.add_parser("verify", help="dry-run replay to verify the artifact")
    v.add_argument("name")
    v.add_argument("--input", action="append", default=[], help="key=value input")

    sub.add_parser("telemetry", help="success/business/failure rates per capability")
    sub.add_parser("failures", help="list the unresolved failure inbox")

    rp = sub.add_parser("replay-case", help="reproduce a failure with its original inputs")
    rp.add_argument("run_id")

    rs = sub.add_parser("resolve", help="mark a failure case resolved")
    rs.add_argument("run_id")

    args = parser.parse_args()
    if args.cmd == "list":
        cmd_list()
    elif args.cmd == "edit":
        cmd_edit(args)
    elif args.cmd == "bump":
        cmd_bump(args)
    elif args.cmd == "verify":
        cmd_verify(args)
    elif args.cmd == "telemetry":
        cmd_telemetry()
    elif args.cmd == "failures":
        cmd_failures()
    elif args.cmd == "replay-case":
        cmd_replay_case(args)
    elif args.cmd == "resolve":
        cmd_resolve(args)


if __name__ == "__main__":
    main()
