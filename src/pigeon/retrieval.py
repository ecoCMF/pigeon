"""Hybrid retrieval: ripgrep (lexical recall) + BM25 (ranking).

``query(text, config, top_k)`` returns bounded, ranked slices of the repo and
the generated manifest — never the whole repo. ripgrep finds which chunks
actually contain query terms (lexical recall); BM25 ranks the chunk corpus
(robust to term frequency / length). The two are blended.

Vector search is optional, behind ``retrieval.vector.enabled`` (off by default),
and local-only — there is no cloud path and no network use when disabled.
"""

from __future__ import annotations

import fnmatch
import glob
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from rank_bm25 import BM25Okapi

from .config import Config

_WORD_RE = re.compile(r"[a-z0-9]+")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SNIPPET_CAP = 1500
_BM25_WEIGHT = 0.6
_LEXICAL_WEIGHT = 0.4


@dataclass
class Result:
    source: str
    start_line: int
    end_line: int
    score: float
    snippet: str
    lexical_hits: int = 0

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "score": round(self.score, 4),
            "lexical_hits": self.lexical_hits,
            "snippet": self.snippet,
        }


@dataclass
class _Chunk:
    source: str
    start_line: int
    end_line: int
    text: str
    tokens: list[str] = field(default_factory=list)


def tokenize(text: str) -> list[str]:
    """Lowercase word/identifier tokens; splits camelCase to help code search."""
    spaced = _CAMEL_RE.sub(" ", text)
    return _WORD_RE.findall(spaced.lower())


SCOPES = ("all", "code", "history", "memory")


def _parse_since(since: str) -> float:
    """ISO date/datetime -> POSIX timestamp (naive values read as UTC)."""
    try:
        dt = datetime.fromisoformat(since)
    except ValueError as exc:
        raise ValueError(
            f"invalid --since value {since!r}: expected ISO format "
            "(e.g. 2026-06-01 or 2026-06-01T12:00:00+00:00)"
        ) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _indexed_files(config: Config, scope: str = "all",
                   since: str | None = None) -> list[str]:
    """Repo-relative posix paths to index (text files within size budget).

    Scopes carve the corpus: ``code`` is the configured include globs (the
    pre-0.3 behavior — hidden event dirs were never globbed), ``history`` is
    the episodic log (handoffs + run manifests), ``memory`` is the distilled
    knowledge under ``.pigeon/memory/``, and ``all`` is their union.
    ``since`` keeps only files modified at/after an ISO date — temporal
    retrieval over the event log without a database.
    """
    if scope not in SCOPES:
        raise ValueError(f"unknown scope {scope!r} (choose from {', '.join(SCOPES)})")
    root = config.root
    cfg = config.retrieval_cfg
    found: set[str] = set()
    if scope in ("all", "code"):
        for pattern in cfg["include"]:
            for hit in glob.glob(pattern, root_dir=str(root), recursive=True):
                found.add(Path(hit).as_posix())
        # Explicitly add generated dotfiles that glob skips (hidden dir).
        for extra in (config.manifest, config.handoff_schema):
            if extra.is_file():
                found.add(extra.relative_to(root).as_posix())
    if scope in ("all", "history"):
        for event_dir in (config.handoffs_dir, config.coordinate_runs_dir):
            if event_dir.is_dir():
                for path in event_dir.glob("*.json"):
                    found.add(path.relative_to(root).as_posix())
    if scope in ("all", "memory"):
        if config.memory_dir.is_dir():
            for path in config.memory_dir.rglob("*.md"):
                found.add(path.relative_to(root).as_posix())
    excludes = cfg["exclude"]
    max_bytes = int(cfg["max_file_bytes"])
    cutoff = _parse_since(since) if since else None
    kept: list[str] = []
    for rel in found:
        if any(fnmatch.fnmatch(rel, pat) for pat in excludes):
            continue
        path = root / rel
        if not path.is_file() or path.stat().st_size > max_bytes:
            continue
        if cutoff is not None and path.stat().st_mtime < cutoff:
            continue
        kept.append(rel)
    return sorted(kept)


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def _chunk_file(rel: str, text: str, chunk_lines: int, overlap: int) -> list[_Chunk]:
    lines = text.splitlines()
    if not lines:
        return []
    step = max(1, chunk_lines - overlap)
    chunks: list[_Chunk] = []
    for start in range(0, len(lines), step):
        window = lines[start : start + chunk_lines]
        if not window:
            break
        body = "\n".join(window)
        if not body.strip():
            continue
        chunks.append(
            _Chunk(source=rel, start_line=start + 1, end_line=start + len(window), text=body)
        )
        if start + chunk_lines >= len(lines):
            break
    return chunks


def _build_corpus(config: Config, scope: str = "all",
                  since: str | None = None) -> list[_Chunk]:
    cfg = config.retrieval_cfg
    chunk_lines = int(cfg["chunk_lines"])
    overlap = int(cfg["chunk_overlap"])
    corpus: list[_Chunk] = []
    for rel in _indexed_files(config, scope=scope, since=since):
        text = _read_text(config.root / rel)
        if text is None:
            continue
        for chunk in _chunk_file(rel, text, chunk_lines, overlap):
            chunk.tokens = tokenize(chunk.text)
            corpus.append(chunk)
    return corpus


def find_ripgrep(config: Config) -> str | None:
    """Locate the ripgrep binary.

    Order: ``PIGEON_RG`` (or legacy ``AGENTCTX_RG``) env var, ``retrieval.ripgrep_path`` config, then
    ``shutil.which("rg")``. The overrides matter in sandboxes where ``rg`` is
    shadowed by a shell function or simply not on PATH.
    """
    for candidate in (os.environ.get("PIGEON_RG"), os.environ.get("AGENTCTX_RG"),
                      config.retrieval_cfg.get("ripgrep_path")):
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    return shutil.which("rg")


def _ripgrep_term_lines(config: Config, terms: list[str]) -> dict[str, dict[str, set[int]]]:
    """For each term, map repo-relative path -> set of matching line numbers.

    Pure lexical recall via ripgrep. Returns empty maps if ripgrep is absent
    (BM25 still works); this keeps the tool functional without rg, though rg is
    the documented dependency.
    """
    rg = find_ripgrep(config)
    out: dict[str, dict[str, set[int]]] = {t: {} for t in terms}
    if not rg:
        return out
    exclude_globs: list[str] = []
    for pat in config.retrieval_cfg["exclude"]:
        exclude_globs += ["-g", f"!{pat}"]
    for term in terms:
        try:
            proc = subprocess.run(
                [rg, "--no-heading", "--line-number", "--hidden", "--color", "never",
                 "-i", "-F", *exclude_globs, "-e", term, "."],
                cwd=config.root, capture_output=True, text=True, timeout=30,
            )
        except subprocess.TimeoutExpired:
            continue  # BM25 still ranks; one stalled term must not hang retrieval
        if proc.returncode not in (0, 1):  # 1 = no matches; >1 = real error
            continue
        for line in proc.stdout.splitlines():
            parts = line.split(":", 2)
            if len(parts) < 3:
                continue
            rel = Path(parts[0]).as_posix()
            try:
                lineno = int(parts[1])
            except ValueError:
                continue
            out[term].setdefault(rel, set()).add(lineno)
    return out


def _lexical_hits(chunk: _Chunk, term_lines: dict[str, dict[str, set[int]]]) -> int:
    hits = 0
    for _term, file_lines in term_lines.items():
        lines = file_lines.get(chunk.source)
        if lines and any(chunk.start_line <= ln <= chunk.end_line for ln in lines):
            hits += 1
    return hits


def query(
    text: str,
    config: Config,
    top_k: int | None = None,
    *,
    use_vector: bool | None = None,
    scope: str = "all",
    since: str | None = None,
) -> list[Result]:
    """Return up to ``top_k`` ranked slices for ``text``. Empty if nothing matches.

    ``scope`` selects the corpus (code / history / memory / all); ``since``
    keeps only files modified at/after an ISO date.
    """
    top_k = top_k if top_k is not None else int(config.retrieval_cfg["default_top_k"])
    query_tokens = tokenize(text)
    if not query_tokens:
        return []

    corpus = _build_corpus(config, scope=scope, since=since)
    if not corpus:
        return []

    vector_on = config.retrieval_cfg["vector"]["enabled"] if use_vector is None else use_vector
    if vector_on:
        return _vector_query(text, config, corpus, top_k)

    bm25 = BM25Okapi([c.tokens for c in corpus])
    scores = bm25.get_scores(query_tokens)
    max_score = max(scores) if len(scores) else 0.0

    unique_terms = sorted(set(query_tokens))
    term_lines = _ripgrep_term_lines(config, unique_terms)
    n_terms = len(unique_terms)

    results: list[Result] = []
    for chunk, raw in zip(corpus, scores):
        bm25_norm = (raw / max_score) if max_score > 0 else 0.0
        hits = _lexical_hits(chunk, term_lines)
        lex_norm = (hits / n_terms) if n_terms else 0.0
        final = _BM25_WEIGHT * bm25_norm + _LEXICAL_WEIGHT * lex_norm
        if final <= 0:
            continue
        snippet = chunk.text if len(chunk.text) <= _SNIPPET_CAP else chunk.text[:_SNIPPET_CAP] + "\n…[truncated]"
        results.append(
            Result(
                source=chunk.source,
                start_line=chunk.start_line,
                end_line=chunk.end_line,
                score=final,
                snippet=snippet,
                lexical_hits=hits,
            )
        )
    results.sort(key=lambda r: (-r.score, r.source, r.start_line))
    return results[:top_k]


def _vector_query(text: str, config: Config, corpus: list[_Chunk], top_k: int) -> list[Result]:
    """Local-only vector retrieval. Off by default; requires the [vector] extra.

    Deliberately minimal: enabling vector on a fast-churn repo is discouraged
    (see the decision record in AGENTS.md). This path raises a clear, actionable
    error rather than silently falling back, so the operator's intent is honored.
    """
    try:
        import chromadb  # noqa: F401, PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "retrieval.vector.enabled is true but the [vector] extra is not "
            "installed. Install it (pip install 'pigeon[vector]') for local "
            "embeddings, or set retrieval.vector.enabled=false to use the "
            "offline lexical+BM25 path."
        ) from exc
    raise NotImplementedError(
        "Local vector retrieval is a documented Phase-1 option but is not wired "
        "in this MVP. Use the default lexical+BM25 path."
    )
