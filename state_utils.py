#!/usr/bin/env python3
"""
Shared helpers for reading/writing competition state.

State file format:
{
  "generated_at": "ISO timestamp",
  "seen": ["link1", "link2"],
  "submitted": ["link3"]
}

Legacy files with an `entries` list are still supported.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Set

logger = logging.getLogger("state_utils")


@dataclass
class CompetitionState:
    seen: Set[str]
    submitted: Set[str]


def _normalize_keys(entries: Iterable[str]) -> Set[str]:
    return {str(item).strip() for item in entries if isinstance(item, str) and str(item).strip()}


def load_state(path: Path) -> CompetitionState:
    if not path.exists():
        return CompetitionState(set(), set())
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to parse state file %s: %s", path, exc)
        return CompetitionState(set(), set())

    if isinstance(data, dict):
        if "seen" in data or "submitted" in data:
            seen = _normalize_keys(data.get("seen", []))
            submitted = _normalize_keys(data.get("submitted", []))
            return CompetitionState(seen, submitted)
        if "entries" in data:  # legacy format
            seen = _normalize_keys(data.get("entries", []))
            return CompetitionState(seen, set())

    logger.warning("State file %s has unexpected format; starting fresh.", path)
    return CompetitionState(set(), set())


def save_state(path: Path, state: CompetitionState) -> None:
    payload = {
        "generated_at": dt.datetime.utcnow().isoformat(),
        "seen": sorted(state.seen),
        "submitted": sorted(state.submitted),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("Updated state file %s", path)
