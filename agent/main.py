"""CLI entry point: run one discovery pass against the mock app.

Usage:
    .venv/bin/python -m agent.main --task "..." [--url http://localhost:9000/]
"""
import argparse
import json
import sys

from playwright.sync_api import sync_playwright

from . import config
from .loop import run_discovery

DEFAULT_TASK = "Search for member 1001, view their detail, then deactivate the account."


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a computer-use discovery pass.")
    parser.add_argument("--task", default=DEFAULT_TASK)
    parser.add_argument("--url", default=config.MOCK_URL)
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--out", help="write the JSON result to this file")
    args = parser.parse_args()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(args.url)
        result = run_discovery(page, args.task, max_steps=args.max_steps)
        browser.close()

    print("\n=== RESULT ===")
    print(json.dumps(result, indent=2, default=str))

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, default=str)
        print(f"\nwrote {args.out}")

    return 0 if result["goal_reached"] else 1


if __name__ == "__main__":
    sys.exit(main())
