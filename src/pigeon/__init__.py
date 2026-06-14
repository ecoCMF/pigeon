"""pigeon — carrier for cross-model agent context (formerly agentctx).

Three decoupled layers:
  1. Canonical context — plain repo files every CLI already reads (AGENTS.md).
  2. Handoff contract  — JSON-schema-validated messages carrying sparse state
     deltas plus pointers (never payloads).
  3. Retrieval         — hybrid lexical (ripgrep) + BM25 over repo + manifest.

See AGENTS.md for the canonical project context.
"""

try:  # single source of truth: installed package metadata (pyproject)
    from importlib.metadata import version as _pkg_version
    __version__ = _pkg_version("pigeon")
except Exception:  # not installed (e.g. vendored checkout)
    __version__ = "0.3.0"
SCHEMA_VERSION = "1.1"

__all__ = ["__version__", "SCHEMA_VERSION"]
