"""Session enumeration: the shared substrate distill and graph both read.

Lives in its own module so ``distill`` and ``graph`` need not import each
other (the audit-flagged cycle): both consume sessions; neither owns them.
"""

from __future__ import annotations

import json
from typing import Any

from .config import Config
from . import coordinate
from . import handoff as ho


def session_handoffs(config: Config, sid: str) -> list[tuple[str, dict[str, Any]]]:
    """(repo-relative path, handoff) pairs for a session, oldest index first."""
    out: list[tuple[str, dict[str, Any]]] = []
    if not config.handoffs_dir.is_dir():
        return out
    for path in sorted(config.handoffs_dir.glob(f"{sid}-*.json")):
        try:
            obj = ho.load_handoff(path, config)
        except (ho.HandoffValidationError, json.JSONDecodeError, OSError):
            continue  # invalid events are reported at run time, not here
        if obj.get("sid") == sid:
            out.append((str(path.relative_to(config.root)), obj))
    return out


def known_sids(config: Config) -> list[str]:
    """Every session id present in handoffs or recorded runs."""
    sids = {run["sid"] for run in coordinate.list_runs(config) if run.get("sid")}
    if config.handoffs_dir.is_dir():
        for path in config.handoffs_dir.glob("*.json"):
            try:
                obj = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if isinstance(obj, dict) and obj.get("sid"):
                sids.add(obj["sid"])
    return sorted(sids)
