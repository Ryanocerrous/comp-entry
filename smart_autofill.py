#!/usr/bin/env python3
"""
Smart Autofill Assistant (CLI wrapper around the shared Selenium workflow).
Usage:
  source .venv/bin/activate
  python smart_autofill.py config.json data.json
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List

from autofill_core import FillAction, load_json, perform_autofill, logger


def _print_preview(actions: List[FillAction], screenshot_path: Path) -> None:
    print("\n=== Autofill preview ===")
    for idx, action in enumerate(actions, 1):
        label = (action.label or "<no label>").strip()
        value_preview = (action.value or "")[:60]
        print(
            f"{idx:02d}. label='{label[:60]}' tag={action.tag} type={action.input_type} "
            f"score={action.score} mapped={action.mapped_key} filled={action.filled} "
            f"value_preview={value_preview}"
        )
    print(f"\nScreenshot saved: {screenshot_path}")
    print("Please review the browser page. Do not proceed if anything looks wrong.")
    print("You can edit fields manually in the browser before confirming.")


def _confirm_submit(actions: List[FillAction], screenshot_path: Path) -> bool:
    _print_preview(actions, screenshot_path)
    response = input("\nType YES to submit automatically, anything else to abort: ").strip().lower()
    if response == "yes":
        logger.info("User confirmed submission.")
        return True
    logger.info("User aborted submission.")
    print("Aborted. Exiting without submitting.")
    return False


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: python smart_autofill.py config.json data.json")
        raise SystemExit(1)

    config_path = Path(sys.argv[1])
    data_path = Path(sys.argv[2])

    cfg = load_json(config_path)
    data = load_json(data_path)

    if not cfg.get("url"):
        cfg["url"] = input("Enter URL to open: ").strip()

    outcome = perform_autofill(cfg, data, confirm_submit=_confirm_submit)

    if outcome.error:
        print(f"\nError: {outcome.error}")
        raise SystemExit(1)

    print("\n=== Run summary ===")
    print(f"Submitted: {outcome.submitted}")
    if outcome.screenshot_path:
        print(f"Preview screenshot: {outcome.screenshot_path}")
    if outcome.post_submit_screenshot_path:
        print(f"Post-submit screenshot: {outcome.post_submit_screenshot_path}")
    if outcome.aborted_reason:
        print(f"Aborted reason: {outcome.aborted_reason}")


if __name__ == "__main__":
    main()

