"""Handoff contract: build, validate, serialize, append.

A handoff is a JSON message carrying sparse state deltas plus pointers. It is
validated against ``.pigeon/handoff.schema.json`` (JSON Schema draft 2020-12)
**on receipt**, and appended to ``.pigeon/handoffs/`` as ``<sid>-<n>.json``.
Logs are append-only; handoffs are never rewritten in place.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator

from . import SCHEMA_VERSION
from .config import Config

_FILENAME_RE = re.compile(r"^(?P<sid>.+)-(?P<n>\d+)\.json$")


class HandoffValidationError(ValueError):
    """A handoff failed schema validation. Message lists every violation."""


def build_handoff(
    *,
    sid: str,
    frm: str,
    to: str,
    done: list[str],
    doing: str,
    artifacts: list[str] | None = None,
    decisions: dict[str, Any] | None = None,
    rag: dict[str, Any] | None = None,
    constraints: dict[str, Any] | None = None,
    crew: dict[str, Any] | None = None,
    context_ref: str | None = None,
    schema_version: str = SCHEMA_VERSION,
) -> dict[str, Any]:
    """Construct a handoff dict. Optional fields are omitted when empty."""
    state: dict[str, Any] = {"done": list(done), "doing": doing}
    if artifacts:
        state["artifacts"] = list(artifacts)
    if decisions:
        state["decisions"] = dict(decisions)
    handoff: dict[str, Any] = {
        "schema_version": schema_version,
        "sid": sid,
        "from": frm,
        "to": to,
        "state": state,
    }
    if rag:
        handoff["rag"] = dict(rag)
    if constraints:
        handoff["constraints"] = dict(constraints)
    if crew:
        handoff["crew"] = dict(crew)
    if context_ref is not None:
        handoff["context_ref"] = context_ref
    return handoff


def load_schema(config: Config) -> dict[str, Any]:
    path = config.handoff_schema
    if not path.is_file():
        raise FileNotFoundError(f"Handoff schema not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def validate_handoff(
    handoff: dict[str, Any],
    config: Config,
    schema: dict[str, Any] | None = None,
) -> None:
    """Validate a handoff against the schema. Raise with a clear, full message."""
    schema = schema if schema is not None else load_schema(config)
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(handoff), key=lambda e: list(e.absolute_path))
    if errors:
        lines = []
        for err in errors:
            loc = "/".join(str(p) for p in err.absolute_path) or "<root>"
            lines.append(f"  - at {loc}: {err.message}")
        raise HandoffValidationError(
            "Invalid handoff (" + str(len(errors)) + " error(s)):\n" + "\n".join(lines)
        )


def serialize_handoff(handoff: dict[str, Any]) -> str:
    """Canonical JSON for a handoff (sorted keys, trailing newline)."""
    return json.dumps(handoff, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _next_index(handoffs_dir: Path, sid: str) -> int:
    if not handoffs_dir.is_dir():
        return 1
    highest = 0
    for child in handoffs_dir.iterdir():
        match = _FILENAME_RE.match(child.name)
        if match and match.group("sid") == sid:
            highest = max(highest, int(match.group("n")))
    return highest + 1


def next_handoff_path(config: Config, sid: str) -> Path:
    """Next append-only path ``<sid>-<n>.json`` for a session."""
    return config.handoffs_dir / f"{sid}-{_next_index(config.handoffs_dir, sid)}.json"


def claim_path(directory: Path, name_for: Callable[[int], str]) -> Path:
    """Atomically claim the next free numbered file (no TOCTOU).

    ``name_for(n)`` -> filename for attempt ``n``. The file is created with
    O_CREAT|O_EXCL, so two concurrent writers can never claim the same slot —
    the loser just moves to the next index. Returns the claimed (empty) path.
    """
    directory.mkdir(parents=True, exist_ok=True)
    n = 1
    while True:
        candidate = directory / name_for(n)
        if not candidate.exists():
            try:
                os.close(os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                return candidate
            except FileExistsError:
                pass  # raced: another writer claimed it between checks
        n += 1


def write_handoff(
    handoff: dict[str, Any],
    config: Config,
    *,
    validate: bool = True,
) -> Path:
    """Validate (by default) then append the handoff. Returns the written path."""
    if validate:
        validate_handoff(handoff, config)
    start = _next_index(config.handoffs_dir, handoff["sid"])
    path = claim_path(config.handoffs_dir,
                      lambda n, s=handoff["sid"], b=start: f"{s}-{b + n - 1}.json")
    path.write_text(serialize_handoff(handoff), encoding="utf-8")
    return path


def load_handoff(path: Path | str, config: Config, *, validate: bool = True) -> dict[str, Any]:
    """Load a handoff from disk, validating on receipt by default."""
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    if validate:
        validate_handoff(obj, config)
    return obj
