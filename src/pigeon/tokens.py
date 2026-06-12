"""Token counting and accounting.

Every handoff and every retrieval payload is counted and appended to
``.pigeon/metrics.jsonl`` alongside a *baseline* — the token cost of the
naive alternative — so savings are measured, not assumed:

- **Handoff baseline:** the same information re-transmitted as prose with every
  pointer's content inlined (because separate CLIs share no store). The actual
  handoff carries pointers only. The delta is what pointers save.
- **Retrieval baseline:** the full contents of the source files. The actual
  payload is the returned slices. The delta is what retrieval saves over dumping.

Token counts use ``tiktoken`` when installed (exact, via the configured
encoding) and a deterministic offline heuristic otherwise.
"""

from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime, timezone
from typing import Any

from .config import Config
from . import resolve as _resolve

_PIECE_RE = re.compile(r"\s+|\w+|[^\w\s]+")  # punctuation grouped in runs
_enc_cache: dict[str, Any] = {}


def _tiktoken_encoding(name: str):
    if name in _enc_cache:
        return _enc_cache[name]
    try:
        import tiktoken  # noqa: PLC0415
    except ImportError:
        _enc_cache[name] = None
        return None
    try:
        enc = tiktoken.get_encoding(name)
    except Exception:  # unknown encoding name
        enc = tiktoken.get_encoding("cl100k_base")
    _enc_cache[name] = enc
    return enc


def using_tiktoken(encoding: str = "cl100k_base") -> bool:
    """True if exact tiktoken counting is available for ``encoding``."""
    return _tiktoken_encoding(encoding) is not None


def _heuristic_tokens(text: str) -> int:
    """Deterministic, offline token estimate.

    Approximates BPE behavior: long words split into ~4-char subwords,
    punctuation counts ~1 token each, and newlines count as their own token.
    Used only when tiktoken is absent; both sides of every comparison use the
    same counter, so the measured ratio is meaningful regardless.
    """
    total = 0
    for piece in _PIECE_RE.findall(text):
        if piece.isspace():
            total += piece.count("\n")
        elif piece[0].isalnum() or piece[0] == "_":
            total += max(1, math.ceil(len(piece) / 4))
        else:
            # BPE folds punctuation runs ("...", "):", "->") into ~1 token
            # per 3 chars, not 1 per char.
            total += max(1, math.ceil(len(piece) / 3))
    return max(1, total) if text else 0


def count_tokens(text: str, encoding: str = "cl100k_base") -> int:
    """Count tokens in ``text`` (exact via tiktoken, else heuristic)."""
    if not text:
        return 0
    enc = _tiktoken_encoding(encoding)
    if enc is not None:
        return len(enc.encode(text))
    return _heuristic_tokens(text)


def record(config: Config, event: dict[str, Any]) -> dict[str, Any]:
    """Append a token-accounting event (with timestamp) to metrics.jsonl."""
    stored = {"ts": datetime.now(timezone.utc).isoformat(), **event}
    path = config.metrics
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(stored, ensure_ascii=False, sort_keys=True) + "\n")
    return stored


def prune_metrics(config: Config, keep: int = 5000) -> tuple[int, int]:
    """Trim metrics.jsonl to the newest ``keep`` events. Returns (before, after)."""
    path = config.metrics
    if not path.is_file():
        return (0, 0)
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    kept = lines[-max(0, keep):] if keep > 0 else []
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(line + "\n" for line in kept), encoding="utf-8")
    os.replace(tmp, path)
    return (len(lines), len(kept))


def _prose_baseline_for_handoff(handoff: dict[str, Any], config: Config) -> str:
    """Render the naive re-transmission of a handoff: prose + inlined pointers."""
    state = handoff.get("state", {})
    lines = [
        f"Handoff from {handoff.get('from')} to {handoff.get('to')} "
        f"(session {handoff.get('sid')}).",
        f"Completed so far: {', '.join(state.get('done', [])) or 'nothing'}.",
        f"Next, please: {state.get('doing', '')}.",
    ]
    for key, val in (state.get("decisions") or {}).items():
        lines.append(f"Decision — {key}: {val}.")
    if handoff.get("rag"):
        lines.append(f"You'll want to look into: {handoff['rag'].get('query', '')}.")
    for key, val in (handoff.get("constraints") or {}).items():
        lines.append(f"Constraint — {key}: {val}.")

    pointers = list(state.get("artifacts", []))
    if handoff.get("context_ref"):
        pointers.append(handoff["context_ref"])
    if pointers:
        lines.append(
            "Since we share no memory, here is the full content of everything "
            "referenced (inlined because there is no pointer resolver):"
        )
    for pointer in pointers:
        try:
            content = _resolve.resolve(pointer, config).read_text()
        except (OSError, ValueError):
            content = f"[unresolved: {pointer}]"
        lines.append(f"----- {pointer} -----")
        lines.append(content)
    return "\n".join(lines) + "\n"


def account_handoff(
    config: Config,
    handoff: dict[str, Any],
    *,
    path: str | None = None,
    record_event: bool = True,
) -> dict[str, Any]:
    """Count a handoff (pointers) vs its prose+inlined baseline. Records by default."""
    from .handoff import serialize_handoff  # local import avoids a cycle

    encoding = config.tokens_cfg.get("encoding", "cl100k_base")
    actual = count_tokens(serialize_handoff(handoff), encoding)
    baseline = count_tokens(_prose_baseline_for_handoff(handoff, config), encoding)
    event = {
        "kind": "handoff",
        "sid": handoff.get("sid"),
        "from": handoff.get("from"),
        "to": handoff.get("to"),
        "path": path,
        "actual_tokens": actual,
        "baseline_tokens": baseline,
        "saved_tokens": baseline - actual,
        "exact": using_tiktoken(encoding),
    }
    return record(config, event) if record_event else event


def account_retrieval(
    config: Config,
    query_text: str,
    results: list,
    *,
    record_event: bool = True,
) -> dict[str, Any]:
    """Count returned slices vs whole-file baseline. Records by default."""
    encoding = config.tokens_cfg.get("encoding", "cl100k_base")
    actual = sum(count_tokens(r.snippet, encoding) for r in results)
    seen: set[str] = set()
    baseline = 0
    for r in results:
        if r.source in seen:
            continue
        seen.add(r.source)
        try:
            baseline += count_tokens(_resolve.resolve(r.source, config).read_text(), encoding)
        except (OSError, ValueError):
            baseline += count_tokens(r.snippet, encoding)
    event = {
        "kind": "retrieval",
        "query": query_text,
        "results": len(results),
        "actual_tokens": actual,
        "baseline_tokens": baseline,
        "saved_tokens": baseline - actual,
        "exact": using_tiktoken(encoding),
    }
    return record(config, event) if record_event else event


def summarize(config: Config) -> dict[str, Any]:
    """Aggregate metrics.jsonl into totals overall and per kind."""
    path = config.metrics
    overall: dict[str, Any] = {"events": 0, "actual_tokens": 0,
                               "baseline_tokens": 0, "saved_tokens": 0}
    by_kind: dict[str, dict[str, int]] = {}
    if path.is_file():
        with path.open(encoding="utf-8") as fh:
            lines_iter = list(fh)
        for line in lines_iter:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = ev.get("kind", "unknown")
            bucket = by_kind.setdefault(
                kind,
                {"events": 0, "actual_tokens": 0, "baseline_tokens": 0, "saved_tokens": 0},
            )
            for field in ("actual_tokens", "baseline_tokens", "saved_tokens"):
                bucket[field] += int(ev.get(field, 0))
                overall[field] += int(ev.get(field, 0))
            bucket["events"] += 1
            overall["events"] += 1
    saved = overall["saved_tokens"]
    base = overall["baseline_tokens"]
    overall["reduction_pct"] = round(100.0 * saved / base, 1) if base else 0.0
    return {"overall": overall, "by_kind": by_kind}


def format_summary(summary: dict[str, Any], *, exact: bool) -> str:
    """Human-readable metrics report."""
    o = summary["overall"]
    note = "exact (tiktoken)" if exact else "heuristic estimate (install [tokens] for exact)"
    lines = [
        f"pigeon token metrics  [{note}]",
        f"  events:           {o['events']}",
        f"  actual tokens:    {o['actual_tokens']:>8}  (delta handoffs + retrieval slices)",
        f"  baseline tokens:  {o['baseline_tokens']:>8}  (prose re-transmission + whole files)",
        f"  saved tokens:     {o['saved_tokens']:>8}",
        f"  reduction:        {o['reduction_pct']:>7}%",
    ]
    for kind, b in sorted(summary["by_kind"].items()):
        pct = round(100.0 * b["saved_tokens"] / b["baseline_tokens"], 1) if b["baseline_tokens"] else 0.0
        lines.append(
            f"    - {kind:<10} events={b['events']:<3} "
            f"actual={b['actual_tokens']:<7} baseline={b['baseline_tokens']:<7} saved={pct}%"
        )
    return "\n".join(lines)
