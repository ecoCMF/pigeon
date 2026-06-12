"""Context packer: assemble the right context bundle *before* work begins.

The hard problem in agent memory is not retrieval — it is knowing which
context space to load before a task starts. ``pigeon pack "<task>"`` answers
it once, up front: it queries every memory layer (distilled memory, repo map,
code, episodic history), deduplicates, fits the result into a hard token
budget, and writes a single Markdown bundle under ``.pigeon/context/``.

A coordinate task can opt in with ``pack: true`` — the bundle is generated
from the task's ``doing`` and attached to its handoff as a ``repo://``
pointer, so the spawned agent starts warm instead of searching blind.

Bundles are snapshots (gitignored, regenerable); packing is token-accounted
(kind ``pack``): actual = the bundle, baseline = the whole files it slices.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Config
from . import retrieval, tokens

# Budget shares per layer; greedy fill in this order. Memory first (small,
# dense, decisions live there), the map next, code is the meat, history last.
_LAYERS = (
    ("memory", 0.20),
    ("manifest", 0.10),
    ("code", 0.50),
    ("history", 0.20),
)
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(task: str, limit: int = 4) -> str:
    words = [w for w in _SLUG_RE.split(task.lower()) if w][:limit]
    return "-".join(words) or "task"


def _manifest_entries(config: Config, query_tokens: set[str]) -> list[tuple[str, str]]:
    """(source, rendered line) for manifest modules relevant to the task."""
    if not config.manifest.is_file():
        return []
    try:
        modules = json.loads(config.manifest.read_text(encoding="utf-8")).get("modules", [])
    except json.JSONDecodeError:
        return []
    scored: list[tuple[int, str, str]] = []
    for mod in modules:
        symbols = [str(f) for f in mod.get("functions", [])]
        for cls in mod.get("classes", []):
            if isinstance(cls, dict):
                symbols.append(str(cls.get("name", "")))
                symbols += [str(m) for m in cls.get("methods", [])]
            else:
                symbols.append(str(cls))
        names = " ".join([mod.get("path", ""), mod.get("doc") or "", *symbols])
        overlap = len(query_tokens & set(retrieval.tokenize(names)))
        if overlap:
            shown = ", ".join(s for s in symbols if s)[:160]
            scored.append((overlap, mod.get("path", "?"),
                           f"- `{mod.get('path', '?')}` — {mod.get('doc') or ''} ({shown})"))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [(path, line) for _, path, line in scored[:5]]


def pack(
    config: Config,
    task: str,
    *,
    max_tokens: int = 4000,
    top_k: int = 5,
    since: str | None = None,
) -> dict[str, Any]:
    """Build a bounded context bundle for ``task``; returns path + accounting."""
    if not task.strip():
        raise ValueError("pack: task description must not be empty")
    encoding = config.tokens_cfg.get("encoding", "cl100k_base")
    query_tokens = set(retrieval.tokenize(task))

    candidates: dict[str, list[tuple[str, str]]] = {"manifest": _manifest_entries(config, query_tokens)}
    for layer, scope in (("memory", "memory"), ("code", "code"), ("history", "history")):
        hits = retrieval.query(task, config, top_k=top_k, scope=scope,
                               since=since if layer == "history" else None)
        candidates[layer] = [
            (r.source, f"### {r.source}:{r.start_line}-{r.end_line}\n```\n{r.snippet}\n```")
            for r in hits
        ]

    # Greedy fill: per-layer budget, global dedup by source slice.
    sections: dict[str, list[str]] = {layer: [] for layer, _ in _LAYERS}
    sources: set[str] = set()
    seen: set[str] = set()
    used = dropped = 0
    for layer, share in _LAYERS:
        layer_budget = int(max_tokens * share)
        spent = 0
        for source, rendered in candidates.get(layer, []):
            key = rendered.splitlines()[0]
            if key in seen:
                continue
            cost = tokens.count_tokens(rendered, encoding)
            if spent + cost > layer_budget and sections[layer]:
                dropped += 1
                continue
            if used + cost > max_tokens:
                dropped += 1
                continue
            seen.add(key)
            sections[layer].append(rendered)
            sources.add(source)
            spent += cost
            used += cost

    titles = {"memory": "Memory (distilled)", "manifest": "Repo map",
              "code": "Code", "history": "Recent history"}
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [f"# Context bundle — {task}", "",
             f"_Packed {ts} by `pigeon pack`; budget {max_tokens} tokens. "
             "Read this first; resolve pointers only if it is not enough._", ""]
    for layer, _ in _LAYERS:
        if sections[layer]:
            lines += [f"## {titles[layer]}", ""] + sections[layer] + [""]
    if dropped:
        lines.append(f"_{dropped} candidate slice(s) omitted by the token budget._\n")
    bundle_md = "\n".join(lines)

    from . import handoff as ho
    stem = _slug(task)
    path = ho.claim_path(config.context_dir, lambda n: f"{stem}-{n}.md")
    path.write_text(bundle_md, encoding="utf-8")
    rel = str(path.relative_to(config.root))

    baseline = 0
    for source in sources:
        try:
            baseline += tokens.count_tokens(
                (config.root / source).read_text(encoding="utf-8"), encoding)
        except (OSError, UnicodeDecodeError):
            continue
    actual = tokens.count_tokens(bundle_md, encoding)
    tokens.record(config, {
        "kind": "pack", "task": task, "path": rel,
        "actual_tokens": actual, "baseline_tokens": baseline,
        "saved_tokens": max(0, baseline - actual),
    })
    return {
        "path": rel,
        "sections": {layer: len(items) for layer, items in sections.items()},
        "sources": sorted(sources),
        "dropped": dropped,
        "tokens": {"actual_tokens": actual, "baseline_tokens": baseline,
                   "saved_tokens": max(0, baseline - actual)},
    }
