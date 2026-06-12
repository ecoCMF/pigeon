# Pigeon — The Complete Manual

Pigeon is a carrier for cross-model agent context: canonical project context,
schema-validated handoffs, parallel coordination of agent CLIs, distilled
memory, and token accounting — all through the filesystem. No daemons, no
databases. This manual is the full reference; the [README](../README.md) is
the narrative tour.

**Contents:** [Install](#1-install) · [Concepts](#2-concepts) ·
[CLI reference](#3-cli-reference) · [Tasks file](#4-the-tasks-file) ·
[Configuration](#5-configuration-reference) · [Handoff contract](#6-the-handoff-contract) ·
[MCP server](#7-the-mcp-server) · [Runs & events](#8-run-manifests-events-exit-codes) ·
[Memory](#9-the-memory-system) · [Safety & cost](#10-safety-and-cost-model) ·
[Recipes](#11-recipes) · [Troubleshooting](#12-troubleshooting)

---

## 1. Install

```bash
pip install -e .                  # runtime
pip install -e ".[dev]"           # + pytest (and mcp/textual for the test suite)
# extras:
#   [tokens]  exact token counts via tiktoken (else a deterministic heuristic)
#   [vector]  local vector retrieval scaffold (off by default)
#   [mcp]     `pigeon mcp` — serve the contract over the Model Context Protocol
#   [tui]     `pigeon status --tui` — full-screen terminal dashboard
```

Requires Python ≥ 3.11, git (for worktree isolation), and
[ripgrep](https://github.com/BurntSushi/ripgrep) on `PATH` for the lexical
retrieval layer (BM25 still works without it). `PIGEON_RG=/path/to/rg`
overrides discovery (legacy `AGENTCTX_RG` honored).

Scaffold any repository:

```bash
cd /path/to/repo
pigeon init .            # .pigeon/ contract dir, AGENTS.md, gitignore, playbooks
git add -A && git commit -m "pigeon scaffold"   # worktrees need ≥ 1 commit
```

The `agentctx` command is a legacy alias of `pigeon`; repositories scaffolded
before the rename keep their `.agentctx/` directory forever — discovery
prefers `.pigeon/`, falls back to `.agentctx/`, and never migrates.

## 2. Concepts

- **Canonical context.** `AGENTS.md` is the single source of truth and the
  cross-tool standard — Codex, opencode, and Copilot read it directly, so
  they need no extra file. A few CLIs auto-load their *own* filename
  (Claude Code → `CLAUDE.md`, Gemini CLI → `GEMINI.md`); `pigeon refresh`
  generates those as thin pointers back to AGENTS.md (no duplicated prose,
  no drift). With `paths.generated: auto` (the default), pigeon detects which
  of those CLIs are on `PATH` and generates **only** their pointer — never a
  file for a tool that reads AGENTS.md natively, and never one for a tool you
  don't have installed. Pin an explicit list to override (e.g. ship both
  regardless of PATH, as this repo does, or pre-create a file for a CLI
  you're adopting next week). Registry: `pigeon.context.CLI_REGISTRY`.
- **Handoff.** A JSON message validated against
  `.pigeon/handoff.schema.json`: sparse state deltas plus *pointers*, never
  payloads. Append-only under `.pigeon/handoffs/`.
- **Coordination.** `pigeon coordinate` fans a tasks file out to agent CLIs
  (claude, agy, opencode, anything with an argv template) as parallel
  subprocesses, scheduled over a dependency DAG.
- **Memory.** Handoffs + run manifests are the episodic log; `pigeon distill`
  consolidates them into committed Markdown (sessions, decision ledger,
  entity graph). `pigeon pack` assembles bounded pre-task context bundles.
- **Measurement.** Every handoff, retrieval, pack, and distill is
  token-accounted into `.pigeon/metrics.jsonl`; child agents that emit JSON
  usage reports get their **measured** consumption recorded too.

## 3. CLI reference

Global: `pigeon --version` · `pigeon --root PATH <cmd>` (run against any
repo; root is otherwise discovered from the cwd upward).

| Command | What it does |
|---|---|
| `init [PATH] [--force] [--with-hook] [--name N]` | Scaffold (idempotent). `--with-hook` installs a pre-commit refresh hook. |
| `refresh` | Rebuild `manifest.json`, regenerate pointer files, project playbook skills into runtime-native subagent files. |
| `retrieve "query" [--top-k N] [--scope code\|history\|memory\|all] [--since ISO] [--json]` | Hybrid ripgrep+BM25 search returning bounded, ranked slices. |
| `handoff --sid S --from A --to B --doing STEP [--done STEP]... [--artifact PTR]... [--decision k=v]... [--constraint k=v]... [--rag-query Q] [--context-ref REF] [--no-write]` | Build, validate, append a handoff; prints token cost. Also `--validate FILE` and `--json-in -`. |
| `plan tasks.yaml [--json]` | **Read-only** preview: execution waves, per-task badges, longest chain, preflight verdict. Writes nothing. Exit 2 if preflight refuses. |
| `coordinate tasks.yaml [--parallel-limit N] [--log-dir D] [--skip-permissions] [--telemetry] [--budget-tokens N] [--budget-usd X] [--dry-run]` | The fan-out. See §4 and §10. |
| `status [SID] [--watch] [--interval S] [--tui]` | Glanceable view of the latest run (live or finished). `--watch` re-reads the manifest file; `--tui` needs the `[tui]` extra. |
| `runs [SID] [--json] [--timeline] [--by-agent] [--critical-path]` | Run history; the three flags render post-mortem reports from the event stream. |
| `cleanup [--keep-runs N]` | Crash reconciliation: removes orphan worktrees (branches survive); prunes old run manifests + event streams. |
| `distill [SID]` | Consolidate handoffs + runs into committed memory (default: all sessions). |
| `pack "task" [--max-tokens N] [--top-k N] [--since ISO] [--json]` | One bounded pre-task context bundle from memory + repo map + code + history. |
| `graph [QUERY] [--hops N] [--rebuild] [--json]` | Query the derived entity graph; no query prints stats. |
| `metrics [--prune N]` | Token-accounting report; `--prune` keeps the newest N events. |
| `mcp` | Serve the contract over MCP (stdio). Needs `[mcp]`. |
| `demo` | Whole-pipeline acceptance demo over the repo's own files. |

## 4. The tasks file

YAML or JSON. Complete schema:

```yaml
sid: sprint-42                # session id — [A-Za-z0-9._-] only
tasks:
  - id: api                   # required; [A-Za-z0-9._-] (becomes filename/branch/args)
    doing: implement /v1/exchange-events     # required; the single step
    runner: claude            # key into coordinate.runners; see routing below
    needs: [schema]           # dependency DAG (acyclic; validated)
    done: [design]            # prior steps, carried into the handoff
    artifacts: ["repo://src/api.py"]          # pointers, resolved on demand
    decisions: {auth: oauth2_pkce}
    constraints: {fail_fast: true}            # merged over the safety set
    rag: {query: "exchange events", top_k: 3}
    context_ref: manifest@HEAD
    isolation: worktree       # throwaway git worktree + branch pigeon/<run>/<task>
    pack: true                # attach a packed context bundle to the handoff
    pack_max_tokens: 4000
    telemetry: true           # append runner's JSON flags; record measured tokens
    readonly: true            # no writes: hard read-only constraint + worktree
                              #   containment by default (override with isolation:)
    mutates_packages: false   # true => requires conda env / venv / container
    prompt: "..."             # override the default prompt template
    crew:                     # deterministic staffing (handoff schema 1.1)
      skills: [advanced-python-backend]
      subagents:
        - role: adversarial-reviewer
          skill: security-audit
          doing: review the diff
          verdict: must approve before hand-back
```

**Runner routing.** A task with no `runner:` is *refused* unless
`coordinate.default_runner` is set — a string routes all unassigned tasks
there; a **list round-robins** across it. Explicit runners always win.
Pigeon never silently routes work to a runner you didn't choose.

**Scheduling.** Tasks form topological waves (`pigeon plan` shows them).
A task launches when everything it `needs` has exited 0; everything
downstream of a failure is *skipped*, never run. `parallel_limit` throttles
concurrency within the run.

**Prompt templates.** Runner argv templates may use `{prompt}`, `{handoff}`,
`{task_id}`, `{sid}`, `{root}`. The default prompt tells the agent to read
its handoff, honor constraints, do the `doing` step, and hand back to
`Coordinator` via `pigeon handoff`.

## 5. Configuration reference

`.pigeon/config.yaml` (or legacy `.agentctx/config.yaml`), deep-merged over
defaults. Every key:

```yaml
schema_version: "1.0"          # config-format version (NOT the handoff schema)

paths:
  canonical: AGENTS.md
  generated: auto                        # detect installed CLIs (below), or an
                                         #   explicit list to force specific files
  manifest: .pigeon/manifest.json
  handoffs_dir: .pigeon/handoffs
  metrics: .pigeon/metrics.jsonl
  handoff_schema: .pigeon/handoff.schema.json
  memory_dir: .pigeon/memory
  context_dir: .pigeon/context           # pack bundles (gitignored)

manifest:
  include: ["src/**/*.py", "*.py"]       # globs whose public API gets indexed
  exclude: ["**/__pycache__/**"]
  decisions: {}                          # pinned decisions shown in the manifest
  owners: {}

retrieval:
  include: ["**/*.py", "**/*.md", "**/*.json", "**/*.toml", "**/*.sh"]
  exclude: ["**/__pycache__/**", ".git/**", ".venv/**", ...]
  max_file_bytes: 200000
  chunk_lines: 40
  chunk_overlap: 10
  default_top_k: 5
  ripgrep_path: null                     # or PIGEON_RG env var
  vector: {enabled: false, model: all-MiniLM-L6-v2, store_dir: .pigeon/vector}

resolve:
  allow_s3: false                        # s3:// pointers refused unless enabled

tokens:
  encoding: cl100k_base                  # exact with [tokens], else heuristic

coordinate:
  log_dir: .pigeon/coordinate/logs
  runs_dir: .pigeon/coordinate/runs
  events_dir: .pigeon/coordinate/events
  worktrees_dir: .pigeon/coordinate/worktrees
  parallel_limit: 4
  default_runner: null                   # name | [list = round-robin] | null = refuse
  auto_distill: false                    # distill the session when a run finishes
  runners:                               # argv templates per runner
    claude:   [claude, -p, "{prompt}"]
    agy:      [agy, -p, "{prompt}"]
    opencode: [opencode, run, "{prompt}"]
  skip_permissions_flags:                # appended only with --skip-permissions
    claude: [--dangerously-skip-permissions]
    agy:    [--dangerously-skip-permissions]
    opencode: []
  telemetry_flags:                       # appended with --telemetry / telemetry: true
    claude: [--output-format, json]      # only verified runner; a wrong flag
    agy:    []                           #   makes a runner print help and exit
    opencode: []                         # add YOUR runner's real usage flag
  env_allowlist: null                    # list => strict env for children (+ PATH/HOME baseline)
  budget: {tokens: null, usd: null}      # default hard ceilings (measured spend)
  safety:
    require_repo_setup: true             # .git + initialized contract dir
    require_linux: true
    require_isolated_env_for_packages: true
    max_depth: 1                         # PIGEON_DEPTH guard: no agent fork-bombs

skills:
  targets:
    claude: .claude/agents               # playbook pages -> native subagent files
```

## 6. The handoff contract

Validated against JSON Schema (draft 2020-12), version **1.1**:

| Field | Notes |
|---|---|
| `schema_version` | `"1.1"` (pattern-validated; 1.0 handoffs still load) |
| `sid`, `from`, `to` | session id, sender, receiver |
| `state.done` / `state.doing` | deltas + the single next step (required) |
| `state.artifacts` | pointers: `repo://`, `file://`, plain paths, `manifest@<rev>`, `s3://` (opt-in) — resolved on demand by the receiver |
| `state.decisions` | carried decisions, e.g. `{auth: oauth2_pkce}` |
| `rag` | optional retrieval hint `{query, top_k}` |
| `constraints` | freeform; coordinate injects the safety set (fs scope, package policy, escalation) |
| `crew` | optional deterministic staffing: `{skills: [...], subagents: [{role, skill, doing, verdict}]}` — non-empty arrays enforced |
| `context_ref` | logical pointer, e.g. `manifest@HEAD` |

Handoffs are validated **on write and on receipt**, append-only
(`<sid>-<n>.json`, atomically claimed — concurrent writers cannot collide),
and never rewritten. A task that appends a valid handoff back to
`Coordinator` is upgraded from `exited` to `completed` — the **completion
contract**.

## 7. The MCP server

```bash
pip install -e ".[mcp]"
claude mcp add pigeon -- pigeon --root /path/to/repo mcp
# (restart the session; MCP servers connect at startup)
```

Any MCP client (Claude Code, Codex, Gemini CLI, opencode, IDEs) gets
**13 tools**, all running through the same validation and token-accounting
paths as the CLI:

| Tool | Purpose |
|---|---|
| `retrieve(query, top_k?, scope?, since?)` | Ranked, bounded slices (code/history/memory/all). |
| `pack(task, max_tokens?, top_k?, since?)` | One pre-task context bundle; returns its path. Call this *before* starting work. |
| `coordinate_plan(tasks_file)` | Read-only preview: waves, badges, preflight verdict. Call before `coordinate_run`. |
| `coordinate_run(tasks_file, parallel_limit?, log_dir?, skip_permissions?, dry_run?, telemetry?, budget_tokens?, budget_usd?)` | The fan-out; blocks; returns the run manifest. Live output goes to stderr (the stdio stream stays clean). |
| `coordinate_status(sid?, latest?)` | Run manifest(s): per-task status, exits, durations, pointers. |
| `handoff_write(sid, from_agent, to_agent, doing, done?, artifacts?, decisions?, constraints?, rag_query?, rag_top_k?, crew?, context_ref?)` | Append a validated handoff. |
| `handoff_read(path)` / `handoff_validate(path? \| handoff_json?)` | Read / validate (paths confined to the repo root). |
| `distill(sid?)` | Consolidate sessions into committed memory. |
| `graph_query(query?, hops?)` | Multi-hop entity-graph query; no query = stats. |
| `metrics_summary()` | Token totals, by kind, measured vs baseline. |
| `repo_manifest()` | The generated module map — use instead of a file-tree dump. |
| `refresh()` | Rebuild manifest, pointers, projected skills. |

**The coordinator loop** (how an orchestrating agent should drive pigeon):
`coordinate_plan` → fix anything refused → `coordinate_run` (budgets +
telemetry on) → `coordinate_status` → read hand-backs → `distill` →
`graph_query`/`retrieve --scope memory` next session. Config is re-read per
call, so editing `config.yaml` needs no server restart.

## 8. Run manifests, events, exit codes

Every run writes two files, atomically and live:

- `coordinate/runs/<sid>-<n>.json` — current state: per-task
  `queued / running / completed / exited / failed / spawn-failed / skipped /
  dry-run`, exit codes, durations, branches, telemetry, budget spent-vs-max.
- `coordinate/events/<sid>-<n>.jsonl` — the chronological record:
  `run.started`, `handoff.dispatched`, `task.*`, `run.<status>` — rendered by
  `runs --timeline / --by-agent / --critical-path`.

Exit codes: **0** all green · **1** failures / invalid handoffs / budget
skips · **2** refused (preflight or bad tasks file) · **130** aborted
(Ctrl-C: children are terminated, the manifest says `aborted`).

## 9. The memory system

- **Episodic**: handoffs + runs (timestamped, append-only). Query with
  `retrieve --scope history --since 2026-06-01`.
- **Semantic**: `pigeon distill` renders per-session records and a
  cross-session decision ledger with provenance into `.pigeon/memory/` —
  **committed**, so knowledge survives `git clone`. Deterministic: no LLM in
  the loop.
- **Procedural**: `.pigeon/memory/playbooks/*.md`. Pages with YAML
  frontmatter (`name`, `description`, optional `tools`) are *projected* by
  `refresh` into each runtime's native subagent format (Claude Code:
  `.claude/agents/<name>.md`) — one canonical page, every CLI's dialect;
  hand-written files are never clobbered. `crew.skill` names resolve here.
- **Relational**: `graph.json`, derived (never hand-maintained) from handoff
  provenance and `[[wiki-links]]` across memory pages (the directory is an
  Obsidian-compatible vault). Unresolved links become `stub` nodes — memory
  worth writing. `pigeon graph "vessel x" --hops 2`.
- **Pre-task**: `pigeon pack` assembles memory + repo map + code + history
  into one budgeted bundle; `pack: true` attaches it to a task's handoff.

## 10. Safety and cost model

Preflight (refuses before anything spawns): repo is a git checkout with the
contract dir initialized; Linux; package-mutating tasks require an isolated
env (conda/venv/container — detected via signals that propagate to
children); runner binaries exist; nested coordination beyond
`safety.max_depth` is refused (`PIGEON_DEPTH`); task/session ids restricted
to `[A-Za-z0-9._-]`.

Isolation: `isolation: worktree` gives each task a throwaway checkout on its
own branch — work is committed there (diffstat in the manifest), handoffs
harvested back, the worktree removed; a rogue agent wrecks a disposable
copy. Unattended flags are appended **only** with `--skip-permissions`.
`env_allowlist` keeps the operator's secrets out of child processes.

Cost (layered, all opt-in but strongly recommended together):

1. **Routing** — `default_runner` so unassigned tasks never land on a
   metered CLI by accident (no default exists; pigeon refuses instead).
2. **Telemetry** — `--telemetry` / per-task `telemetry: true` makes children
   emit JSON usage; measured tokens + cost land in the manifest and metrics.
3. **Budgets** — `--budget-tokens` / `--budget-usd`: hard ceilings on
   *measured* spend; once crossed, nothing new launches, the rest is
   recorded as skipped. Budgets can only see what telemetry measures —
   pair them.

How an agent staffs its work internally is its own judgment; contract a
`crew:` when you want staffing decided deterministically.

**Read-only / untrusted tasks.** A prompt-level "don't modify files" is
*soft* — an agent or one of its subagents can ignore it (and will, given a
plausible reason). The only *hard* guarantee is `isolation: worktree`, where
every write lands on a disposable branch and never the working tree. Declare
`readonly: true` and pigeon does both: injects the read-only constraint
*and* defaults the task to a worktree (override with explicit
`isolation: shared` only if you accept the risk). Review, audit, and
analysis tasks should always be `readonly: true`.

## 11. Recipes

**Spread load off the metered CLI:**
```yaml
coordinate:
  default_runner: [agy, opencode]
```

**The tournament** (exploit finetuning diversity):
```yaml
sid: tourney
tasks:
  - {id: api-claude,   runner: claude,   doing: &t implement /v1/events, isolation: worktree, telemetry: true}
  - {id: api-agy,      runner: agy,      doing: *t, isolation: worktree, telemetry: true}
  - {id: api-opencode, runner: opencode, doing: *t, isolation: worktree, telemetry: true}
  - id: judge
    runner: claude
    needs: [api-claude, api-agy, api-opencode]
    doing: compare the three branches (diffstats in the run manifest), pick a winner, record the choice as a decision in your hand-back
    crew:
      subagents:
        - {role: correctness-judge, skill: advanced-python-backend}
        - {role: security-judge,    skill: security-audit}
```
Three solutions on three branches, measured cost per contestant, and the
verdict flows into the decision ledger via `distill`.

**CI gate:** `pigeon plan tasks.yaml` exits 2 on any preflight violation —
cheap to run in a pipeline before anything spends a token.

**Crash recovery:** `pigeon cleanup --keep-runs 20` — orphan worktrees
removed (branches kept), history bounded.

## 12. Troubleshooting

| Symptom | Meaning / fix |
|---|---|
| `preflight: repository is not set up` | Run `git init` + `pigeon init` — agents may modify a folder only when changes are revertible. |
| `task(s) ... name no runner` | Set `runner:` per task or `coordinate.default_runner` (see §4). |
| `worktree isolation needs ... at least one commit` | Make the scaffold commit first. |
| Exit 1 with `skipped` tasks | Check `skipped_because` in `pigeon status` — a failed dependency or an exhausted budget. |
| Budget never trips | Budgets count *measured* spend — enable telemetry. |
| `mutates_packages` refused | Run inside a conda env/venv/container; detection uses env vars that children inherit. |
| Handoffs with `crew:` rejected as invalid | Your repo's `handoff.schema.json` predates the field — run `pigeon refresh`, which upgrades a strictly-older schema in place. |
| A runner prints its help and exits under `--telemetry` | That runner has no JSON-usage flag; set `coordinate.telemetry_flags.<runner>: []` (default for agy/opencode). |
| A "read-only" task edited files anyway | Prompt constraints are soft. Mark it `readonly: true` (auto-worktree containment) or set `isolation: worktree`. |
| Failed task, cause unclear | `pigeon status` shows the log tail under the task; full log path is in the manifest. |
| Coordinator crashed (OOM/SIGKILL) | `pigeon cleanup` — worktrees reconciled, branches preserved. |
| MCP tools absent after `claude mcp add` | Servers connect at session startup — restart the session. |
| `the TUI needs ... textual` | `pip install "pigeon[tui]"`, or use `status --watch`. |

---

*Generated docs (`CLAUDE.md`, `GEMINI.md`, manifest, projected skills) are
rebuilt by `pigeon refresh` — edit `AGENTS.md` and the playbooks, never the
outputs. The contract is the filesystem; everything else is a view.*
