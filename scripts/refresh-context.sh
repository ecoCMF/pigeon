#!/usr/bin/env bash
# Regenerate the project manifest and sync CLAUDE.md / GEMINI.md from AGENTS.md.
# Wire this into a git pre-commit hook so generated context never goes stale:
#
#   ln -sf ../../scripts/refresh-context.sh .git/hooks/pre-commit
#
# Prefers the installed `agentctx` console script; falls back to the module.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if command -v agentctx >/dev/null 2>&1; then
    agentctx refresh "$@"
else
    python -m agentctx.cli refresh "$@"
fi
