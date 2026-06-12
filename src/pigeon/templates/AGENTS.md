# AGENTS.md — canonical context for {PROJECT}

> **This is the single source of truth.** `CLAUDE.md` and `GEMINI.md` are
> generated pointers to this file and must never be edited by hand. Run
> `pigeon refresh` (or `make context`) after editing this file.

## Goal

<!-- TODO: one paragraph — what this project is and what it's for. -->

## Architecture

<!-- TODO: the major components and how they fit together. The machine-readable
map is the generated manifest at `.agentctx/manifest.json` (modules, public
interfaces, entry points). Read the manifest instead of dumping the file tree. -->

## Key decisions

<!-- TODO: decisions that aren't obvious from the code, and why. Mirror the
important ones into `.agentctx/config.yaml` under `manifest.decisions` so they
land in the manifest. -->

## How agents should behave

- **Retrieve, don't dump.** Use `pigeon retrieve "<query>"` to pull bounded,
  ranked slices. Do not paste whole files or the repo tree into your context.
- **Hand off via the contract.** To pass work to another runtime or sub-agent,
  emit a handoff validated against `.agentctx/handoff.schema.json`
  (`pigeon handoff ...`); carry **pointers, not payloads**. The receiver
  resolves them on demand. Handoffs are appended to `.agentctx/handoffs/`.
- **Resolve pointers on demand.** Use the shared resolver; never inline artifact
  contents into a handoff.
- **Measure.** Every handoff and retrieval is token-accounted; see
  `pigeon metrics`. Prefer the approach the metrics show to be cheaper.

Separate CLIs share no memory. The contract is the filesystem, not anyone's
context window.

## Conventions

<!-- TODO: language/version, tests, style, anything an agent must respect. -->
