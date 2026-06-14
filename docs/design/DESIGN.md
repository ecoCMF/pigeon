# DESIGN — First-Class Multi-Model ("Army") Support for Pigeon

**Authority:** `verdict` (Claude Opus 4.8) — session `army-design`
**Date:** 2026-06-13
**Inputs reconciled:** `proposal-gen-deepseek.md`, `proposal-gen-mimo.md`,
`proposal-gen-north.md`, `review-triage.md` (Sonnet gate), `review-concord.md`
(agy concordance gate).

This document renders a verdict on every flagged issue, adjudicates the points
where the two reviews disagree, and specifies a single implementable design.
Every verdict is grounded in the actual source (file:line anchors verified
against the working tree, not the proposals' paraphrases).

---

## 0. Grounding facts (verified against source)

These five facts decide most of the verdicts, so they are stated up front:

1. **`_fill()` leaves unmatched placeholders literal** (`coordinate.py:827-832`):
   it only replaces keys present in the `subs` dict. A `{model}` in a template
   with no `model` in `subs` is passed *verbatim* to the CLI → guaranteed
   runtime failure. → kills "leave `{model}` literal" (deepseek) and
   "`--model {model}` in default templates" (north).
2. **Handoffs are append-only, never rewritten in place** (`handoff.py:6`;
   `write_handoff` claims a fresh `<sid>-<n>.json` via `claim_path`). → any
   "rewrite the handoff at spawn" scheme is illegal; the cross-wave mechanism
   must *defer the single write*, not rewrite.
3. **`state.artifacts` already exists in schema v1.1** as a pointer array
   (`.pigeon/handoff.schema.json`; `SCHEMA_VERSION = "1.1"`). → injecting
   upstream pointers needs **no schema bump**; deepseek's "v1.2 + supplements
   array" is unnecessary.
4. **BudgetTracker is fed only after a child exits** (`_run_task` mines stdout;
   `coordinate.py:951-968`). The coordinator has zero visibility into in-flight
   token usage. → an in-coordinator TPM ledger cannot prevent a parallel-wave
   burst; concord is correct on mechanism.
5. **Worktree isolation commits artifacts to a branch then removes the tree**
   (`_worktree_commit_and_remove`, `coordinate.py:930-947`); only handoffs are
   harvested back to the main checkout (`_worktree_finish`, 897-927). A
   downstream `repo://` pointer to an isolated task's *file* artifact resolves
   against `config.root`'s working tree (`resolve.py:112-114`) and will
   `FileNotFoundError`. → the worktree paradox is real, but **conditional on
   `isolation: worktree`**. The current `army-design` run wrote proposals to the
   shared tree (they exist on disk now), so the common path is unaffected.

All handoffs + commands are built **up-front** in one pass (`coordinate.py:1203-1244`)
and `--dry-run` prints exactly those commands (1246-1252) — the constraint any
deferred-handoff design must not break.

---

## 1. Verdicts on flagged issues

`ACCEPT` = the flag stands and the fix is adopted. `REJECT` = the flag is
overruled. Each row gives the one-line reasoning and the concrete fix carried
into the design.

### 1a. `model:` field / `{model}` placeholder

| ID | Flag | Verdict | Reasoning | Fix adopted |
|----|------|---------|-----------|-------------|
| DS-1 / MM-strip / C-a-1 | Post-substitution stripping of `-m ""` is fragile | **ACCEPT** | A token-filter pass breaks on `--model={model}` or reordered argv; `_fill` has no notion of flag pairing. | No stripping. `{model}` is added to `subs` **only when a model is resolved**; otherwise it is never substituted. |
| C-a-2 | deepseek leaves `{model}` literal when unset → runtime failure | **ACCEPT** | Fact #1: literal `{model}` reaches the CLI. | Preflight rejects a plan where a chosen runner template contains `{model}` but the task has no resolved model. |
| N-2 | north puts `--model {model}` in claude/agy defaults → breaks existing tasks | **ACCEPT** | Fact #1 again: every existing model-less claude task would ship a literal `{model}`. | **Default templates stay exactly as today** (`claude`/`agy`/`opencode`, no `{model}`). Model-bearing templates are opt-in per project. |
| N-5 | "no model + multi-model = hard fail" breaks backward compat | **ACCEPT** | Existing tasks files round-robin runners with no model and must keep working. | Missing `model:` ⇒ no `{model}` substitution, template used as-is. Error *only* via the preflight rule above. |

### 1b. Model pools + round-robin

| ID | Flag | Verdict | Reasoning | Fix adopted |
|----|------|---------|-----------|-------------|
| (consensus) | `model: pool:<name>` string-prefix vs. separate `model_pool:` field | **ACCEPT (separate field)** | Both gates agree: a string-prefix conflates two YAML types and complicates schema validation. | A distinct optional task field `model_pool:`. |
| C-b-1 | north's weighted distribution | **ACCEPT (drop weighting)** | Weighting adds tuning surface with no demonstrated need; round-robin is sufficient and auditable. | Plain round-robin only. |
| C-b-fix | seed the round-robin index per session | **ACCEPT (with correction)** | Good: stops every concurrent session hammering `pool[0]` first — directly serves the free-model spread goal. **Correction:** Python's builtin `hash()` is `PYTHONHASHSEED`-salted and non-reproducible across processes; use a stable digest. | `offset = int(sha1(sid).hexdigest(),16) % len(pool)`; `model = pool[(offset + i) % len(pool)]`, `i` = index among tasks using *that* pool, in task-definition order. Deterministic per `sid`, varied across `sid`s. |
| N-6 | per-individual-model budget map → config explosion | **ACCEPT** | Pre-registering every model string does not scale with large pools. | Throttle config attaches to the **pool**, not to individual model strings. |

### 1c. Army → gate → verdict topology

| ID | Flag | Verdict | Reasoning | Fix adopted |
|----|------|---------|-----------|-------------|
| DS-3 | concordance `with: <task_id>` hardwires IDs into task defs | **ACCEPT** | Couples a task to a sibling's name; blocks templating. | Concordance is expressed (if at all) at the `topology:` level, not inside a task. |
| C-c-1 | concordance *strategies* (`majority`/`unanimous`) in the coordinate layer | **ACCEPT** | Voting/consensus is agent-level reasoning. The coordination layer's contract is "deps exited 0", not "interpret reviews". Putting strategy here leaks reasoning into scheduling. | **No strategy field.** Concordance is just a parallel reviewer task wired by `needs:`; the verdict agent reconciles. (This session is the proof.) |
| C-c-2 | implicit `army:` macro expansion (deepseek/north) hurts plan visibility | **ACCEPT** | A 1-line block exploding into 7 hidden tasks makes `--dry-run` un-auditable. | **No macro in v1.** Tasks stay explicit. `topology:` is annotation only. A future task-*emitter* that writes an inspectable expanded file may be reconsidered if demand appears. |
| MM-2 | topology validation contradiction (advisory vs. `load_tasks` rejects) | **ACCEPT** | A pure annotation must not reject a valid DAG. | `topology:` drives display + warn-only checks; never a hard error. |
| MM-3 | inter-wave injection (early phase) hidden-depends on topology (late phase) | **ACCEPT** | Injection can derive waves from `needs:`/`compute_waves()` alone. | Injection is independent of `topology:`; topology adds display/shorthand only. |
| N-4 | `species: army`, `wave_count` are undefined fields | **ACCEPT** | They map to no existing structure. | Removed. Use `topology:` annotation. |

### 1d. Cross-wave pointers-not-payloads

| ID | Flag | Verdict | Reasoning | Fix adopted |
|----|------|---------|-----------|-------------|
| DS-2 / C-d-1 | supplement handoff files + `{supplement_handoff}` placeholder | **ACCEPT** | Two issues: (i) a new placeholder needs agent cooperation or artifacts are silently dropped; (ii) Fact #3 — `state.artifacts` already exists, so a second file is pure overhead (disk tracking, non-atomic token accounting, cleanup). | Inject resolved pointers into the **primary handoff's `state.artifacts`**. No second file, no new placeholder. |
| MM-1 | `upstream_pointers()` emits worktree *directory* pointers | **ACCEPT** | The resolver treats `repo://` as a file (`.is_file()`); a directory is "not exists". | Inject only file artifacts + harvested return-handoff paths. Existence-check every pointer before injecting (see §2d). |
| C-GAP-1 | **worktree isolation paradox** — isolated upstream file artifacts don't exist on the main tree | **ACCEPT (key finding)** | Fact #5. The Sonnet gate did miss this. | v1 **constraint + preflight warning** (don't block the common case); `git://<rev>:<path>` resolver deferred to a later phase to lift it. See §2d / §5. |
| C-GAP-3 | dry-run breaks if handoffs are built at runtime | **ACCEPT** | Fact: `--dry-run` prints up-front-built commands. | Up-front pass still builds/accounts a **speculative** handoff for `receives:` tasks and prints the command; the *write* is deferred to spawn. See §2d. |
| (adopt) | mimo's two-phase dynamic build, pointers into `state.artifacts` | **ACCEPT (refined)** | Correct mechanism, but must honor Fact #2 (append-only). | Refined to a **per-task deferred write at spawn** (not a per-wave rebuild) — one file, written once. See §2d. |

### 1e. Telemetry + rate-limit handling for free models

| ID | Flag | Verdict | Reasoning | Fix adopted |
|----|------|---------|-----------|-------------|
| N-3 | `can_consume()` subtracts a time value from a token counter | **ACCEPT (moot)** | Genuine bug, but the whole in-coordinator token ledger is dropped (next row), so it never gets built. | — |
| C-e-1 / C-GAP-2 vs. triage "KEEP RateLimitTracker" | **the disagreement:** should the coordinator run an RPM/TPM sliding-window ledger? | **ACCEPT concord / OVERRULE triage** | Fact #4: telemetry arrives only post-exit, so a TPM ledger is blind exactly when it matters (a parallel wave). triage endorsed the deque tracker on code-cleanliness grounds; concord is right on *mechanism*. | **No TPM ledger, no telemetry-fed RPM ledger.** Replace with what the coordinator *can* enforce from its own clock (§2e). |
| N-7 | 8-week timeline mismatches session cadence | **ACCEPT** | pigeon ships in single-commit sessions (0.1.x). | Session-based phases (§4). |
| (salvage) | wall-clock `deadline_minutes`; per-event `model` field; retry on 429/503 | **ACCEPT** | All clock-only or post-hoc — genuinely enforceable. | Kept, reshaped in §2e. |

**Adjudicated rate-limit verdict (the one real conflict).** The coordinator
owns exactly two signals without child cooperation: **its own spawn clock** and
**process exit codes/output**. Therefore:

- **REJECT** any tokens-per-minute ledger and any telemetry-fed requests-per-minute
  gate — proactively unenforceable (Fact #4).
- **ACCEPT** spawn-side throttles that need only a clock: per-pool
  `max_concurrency` and `min_spawn_interval_s`.
- **ACCEPT** `budget.deadline_minutes` (clock-only, checked in the scheduler loop
  beside `budget.exhausted()`).
- **ACCEPT** reactive 429/503 handling as a *coarse safety net*: a child that
  exits with a detected rate-limit signal is re-queued up to the pool's
  `max_retries` after a backoff. Genuine per-call backoff is delegated to the
  runner CLI where it exists (concord's point); the coordinator's retry is the
  floor, not the ceiling.

### 1f. Cross-cutting flags

| ID | Flag | Verdict | Fix |
|----|------|---------|-----|
| N-1 | `model://` URI scheme undefined | **ACCEPT** | `repo://` (and existing schemes) only. No `model://`. The only *new* scheme ever added is `git://` (§5), and only when that phase lands. |

---

## 2. The synthesized design

Five orthogonal, opt-in additions. Absent every new key, existing tasks files
and configs run byte-for-byte as today.

### (a) `model:` task field + `{model}` placeholder

- New optional task field **`model:`** — a concrete provider/model string.
- `_build_command` adds `"model"` to `subs` **iff** the task has a resolved
  model (from `model:` or a resolved `model_pool:`). When absent, `{model}` is
  never substituted (Fact #1 governs).
- **Default runner templates are unchanged.** To use models, a project supplies
  a model-bearing template — e.g. point its `opencode` runner at
  `["opencode", "run", "-m", "{model}", "{prompt}"]` and have every opencode
  task set `model:`. This collapses the current N per-model runners into one.
- **Preflight (`coordinate.py` `preflight`)** gains two checks:
  - **error**: a task's chosen runner template contains `{model}` but the task
    has no resolved model.
  - **warning**: a task sets `model:` but its runner template has no `{model}`
    (the model is silently ignored).

*Rejected mechanisms (do not resurrect): post-hoc `-m ""` stripping; leaving
`{model}` literal; `--model {model}` baked into claude/agy defaults.*

### (b) Named model pools + round-robin

```yaml
coordinate:
  model_pools:
    # bare-list form — simplest, no throttle
    sonnet: [anthropic/claude-sonnet-4-6]
    # object form — carries pool-level throttle (free tiers)
    free-opencode:
      models:
        - opencode/nemotron-3-ultra-free
        - opencode/deepseek-v4-flash-free
        - opencode/mimo-v2.5-free
        - opencode/north-mini-code-free
      max_concurrency: 2       # coordinator spawn cap for tasks on this pool
      min_spawn_interval_s: 5  # stagger spawns of this pool's tasks
      max_retries: 2           # re-queue on detected 429/503 exit
```

- New optional task field **`model_pool:`** (a pool name). Mutually exclusive
  with `model:` on the same task (preflight error if both set).
- Resolution in `load_tasks` (after runner assignment, mirroring the
  `default_runner` round-robin at `coordinate.py:259-265`): for each task using
  pool *P*, in task-definition order, assign
  `pool[(offset_P + i) % len(pool)]` where `offset_P = sha1(sid) mod len(pool)`.
  Deterministic per `sid` (reproducible, auditable), spread across sessions.
- Both list and object pool forms normalize to `{models, max_concurrency,
  min_spawn_interval_s, max_retries}` at load (defaults: unlimited concurrency,
  no interval, `max_retries: 0`).
- Preflight: every referenced pool name must exist and be non-empty.

### (c) `topology:` annotation (display + validation only)

```yaml
sid: army-design
topology:
  army:
    propose: [gen-nemotron, gen-deepseek, gen-mimo, gen-north]
    gate: triage
    concordance: concord
    verdict: verdict
tasks: [ ... explicit, with their own needs: ... ]
```

- Pure semantic annotation. Drives `pigeon plan` to print a readable
  `army → gate · concordance → verdict` view.
- **Warn-only validation** (never rejects a valid `needs:` DAG): warn if
  `gate`/`concordance` don't `needs:` all `propose` tasks, or `verdict` doesn't
  `needs:` both gates.
- **Not required by anything else** — cross-wave injection derives waves from
  `needs:`/`compute_waves()` directly. No macro expansion. No strategy/voting.

### (d) Cross-wave pointers-not-payloads: deferred-write injection

New optional task field **`receives:`** — a list of `repo://`/path glob patterns
(or pointers) the task should be handed once its `needs:` complete:

```yaml
- id: triage
  runner: sonnet
  needs: [gen-nemotron, gen-deepseek, gen-mimo, gen-north]
  receives:
    - "repo://.pigeon/coordinate/brainstorm/proposal-*.md"
```

**Mechanism (honors append-only — Fact #2, and dry-run — Fact #3):**

1. **Up-front pass** (`coordinate.py:1203-1244`), unchanged for tasks *without*
   `receives:` — build + write + account exactly as today.
2. For a task *with* `receives:`: build a **speculative** handoff in memory only
   (resolve globs against the current FS best-effort; usually empty pre-run),
   token-account it, and record the command for `--dry-run`. **Do not write the
   file yet.**
3. **Live scheduler**, at the exact point a `receives:` task becomes ready
   (`deps[tid] <= succeeded`, `coordinate.py:1329-1334`), before `pool.submit`:
   - Resolve `receives:` globs against the now-populated tree; if `receives:` is
     omitted, **auto-collect** the `artifacts:` declared by completed `needs:`
     tasks (mimo's fallback).
   - For each candidate pointer, call `resolve(...).exists()`; **drop + warn**
     any that don't resolve (this is where a worktree-isolated file artifact is
     caught — see constraint below).
   - Build the handoff with the surviving pointers in `state.artifacts`, write
     it **once** via `write_handoff` (append-only honored), build the command
     with that ref, then submit.
4. **`--dry-run`** prints the speculative command with injected artifacts marked
   `(speculative — resolved at spawn)`; no file is written for deferred tasks.

**Worktree constraint (v1):** a cross-wave **file** artifact must live on the
shared working tree — i.e. a task whose file output is `receives:`-d downstream
must **not** use `isolation: worktree`. Handoffs are exempt (they are harvested
back by `_worktree_finish`). Preflight emits a **warning** when a downstream
`receives:` pointer maps to a file produced by an upstream `isolation: worktree`
task. (The full lift is §5.)

*Rejected: supplement handoff files; `{supplement_handoff}` placeholder; schema
v1.2; in-place handoff rewrite; injecting directory pointers.*

### (e) Telemetry + free-model handling

**Throttle (coordinator-enforceable, clock-only):**

- Per-pool `max_concurrency` — cap concurrent in-flight tasks drawing on that
  pool (a second axis inside a wave, on top of the global `parallel_limit`).
- Per-pool `min_spawn_interval_s` — minimum wall-clock gap between spawning two
  tasks on the same pool; smooths the request profile without pre-computed
  stagger.
- `budget.deadline_minutes` — checked in the scheduler loop alongside
  `budget.exhausted()`; once exceeded, remaining `pending` tasks are skipped
  with `skipped_because=["deadline exceeded"]` (reuses the existing skip path at
  `coordinate.py:1306-1313`).

**Reactive safety net:** on a child exit whose output matches a 429/503/rate-limit
signal, re-queue the task after a short backoff up to the pool's `max_retries`,
then fail it. Per-call backoff is delegated to the runner CLI where supported.

**Telemetry:** add a **`model`** field to each `agent_run` event (the resolved
model string). `by_agent_report`/`pigeon metrics` gain per-model aggregation:
tokens, duration, success rate, retry/deadline-skip counts. Cost stays `$0.00`
for free models; the real cost surfaced is **wall-clock contribution** and
**retry pressure**, not a fabricated rate-limit-remaining number.

*Rejected: in-coordinator TPM ledger; telemetry-fed RPM sliding window;
per-individual-model budget map; `ModelRateLimitTracker.can_consume` arithmetic.*

---

## 3. Config & task schema (concrete)

**`config.py` `default_config()` additions** (all default to off/empty):

```python
"coordinate": {
    # ... existing keys unchanged ...
    "model_pools": {},          # name -> [model,...] | {models:[...], max_concurrency, min_spawn_interval_s, max_retries}
    "budget": {
        "tokens": None,
        "usd": None,
        "deadline_minutes": None,   # NEW: clock-only wall-clock ceiling
    },
    # runners / skip_permissions_flags / telemetry_flags: UNCHANGED defaults.
    # Projects opt into models by editing a runner template to include {model}.
}
```

**Task fields** (all optional; unknown keys already tolerated):

```yaml
- id: <str>            # existing
  runner: <str>        # existing — the CLI binary template
  model: <str>         # NEW (a) — concrete model; mutually exclusive with model_pool
  model_pool: <str>    # NEW (b) — pool name; round-robin resolved at load
  receives:            # NEW (d) — globs/pointers injected at spawn after needs complete
    - "repo://...*.md"
  needs: [<id>, ...]   # existing — drives waves and injection
  # ... isolation, crew, readonly, pack, telemetry, etc. unchanged ...
```

**Tasks-file top level:** optional `topology:` block (§2c). No schema bump to
`handoff.schema.json` — `state.artifacts` already carries pointers (Fact #3).

---

## 4. Phased build plan (session-based)

| Phase | Scope | Touches | Gate to next |
|-------|-------|---------|--------------|
| **1 — model seam** | `model:` field, `{model}` in `subs` (only when resolved), preflight error/warning, default templates untouched, `model` in `agent_run` telemetry | `config.py`, `coordinate.py` (`_build_command`, `preflight`, telemetry emit) | Existing `test_coordinate.py` green unchanged; new tests: model substitutes, no-model leaves template as-is, preflight rejects unresolved `{model}`. |
| **2 — pools** | `model_pools` config (both forms, normalized), `model_pool:` field, `sid`-seeded round-robin, preflight pool checks, per-model metrics | `config.py`, `coordinate.py` (`load_tasks`, `by_agent_report`) | Deterministic assignment test (same sid ⇒ same mapping; different sid ⇒ rotated). |
| **3 — cross-wave injection** | `receives:` field, deferred per-task handoff write at spawn, existence-check + drop/warn, dry-run speculative print, worktree preflight warning | `coordinate.py` (run loop, scheduler) | Re-run *this* `army-design` topology end-to-end with `receives:` instead of the prompt-glob; dry-run still prints all commands. |
| **4 — free-pool throttle** | per-pool `max_concurrency` + `min_spawn_interval_s`, `budget.deadline_minutes`, 429/503 re-queue with `max_retries` | `coordinate.py` (scheduler), `config.py` | Wave of N free-pool tasks spawns no more than `max_concurrency` at once, spaced by the interval; deadline skips remaining. |
| **5 — topology annotation** | `topology:` parse, `pigeon plan` display, warn-only validation | `coordinate.py`, `cli.py` | `pigeon plan` prints `army → gate · concordance → verdict`; a mismatched DAG warns, never errors. |
| **6 — (deferred) `git://` resolver** | revision-aware pointer scheme to lift the worktree constraint (§5) | `resolve.py`, `coordinate.py` injection | Only if isolation + cross-wave file artifacts becomes a real need. |

Phases 1–2 deliver the headline win (decouple model from runner, kill the N×K
runner explosion). Phase 3 replaces this session's fragile prompt-glob. Phases
4–6 are independent and can land in any order as need proves out.

---

## 5. Deferred: `git://` revision-aware resolver (lifts the worktree constraint)

When `isolation: worktree` must coexist with cross-wave file artifacts, add one
new pointer scheme to `resolve.py`, mirroring `_resolve_manifest`'s `git show`
pattern (`resolve.py:62-86`):

- `git://<branch-or-rev>:<relpath>` → `git show <rev>:<relpath>` under
  `config.root`, returning bytes (no working-tree file required).
- The coordinator injects `git://pigeon/{run_id}/{task_id}:<relpath>` for an
  isolated upstream task's committed artifacts (the branch from
  `_worktree_commit_and_remove`).

This is the *only* sanctioned new scheme, and only in this phase. Until then the
§2d constraint + preflight warning keep the common (shared-tree) path correct
and the unsupported combination loud rather than silently broken.

---

## 6. Explicitly rejected — do not resurrect

- Supplement handoff files / `{supplement_handoff}` placeholder (DS) — second
  file, agent-cooperation hazard, unnecessary given `state.artifacts`.
- Handoff schema v1.2 "supplements" array — `state.artifacts` already exists.
- In-place handoff rewrite at spawn — violates append-only (Fact #2).
- Concordance strategy/voting in the coordinate layer (`majority`/`unanimous`)
  — agent-level reasoning; keep it out of scheduling.
- `army:` macro expansion into hidden tasks — destroys plan/dry-run visibility.
- `species: army`, `wave_count` — undefined, map to nothing.
- `model://` scheme — no resolver; use `repo://` (and later `git://`).
- `--model {model}` in claude/agy default templates — breaks every existing
  model-less task (Fact #1).
- `{model}` left literal when unset — same runtime failure.
- Post-substitution `-m ""` stripping — fragile argv munging.
- "missing `model:` is a hard error under a `default_runner` list" — breaks
  backward compat.
- Per-individual-model budget map — config explosion; attach to pools.
- In-coordinator TPM ledger / telemetry-fed RPM sliding window — blind during
  parallel waves (Fact #4); replaced by spawn-side throttle + deadline + retry.
- Weighted pool distribution — unneeded; plain round-robin.
- Python builtin `hash(sid)` for seeding — `PYTHONHASHSEED`-salted, not
  reproducible; use `sha1`.

---

## 7. Backward compatibility

| Addition | Absent ⇒ |
|----------|----------|
| `model:` | `{model}` never substituted; template used as-is. |
| `model_pool:` / `model_pools` | no resolution. |
| `{model}` in a template | only ever substituted; preflight guards the unresolved case. |
| `receives:` | up-front handoff build, exactly as today; prompt-glob still works. |
| `topology:` | no display, no validation. |
| pool throttle / `deadline_minutes` / retries | no throttle, no deadline, no retry. |
| `model` telemetry field | additive; existing readers ignore it. |

Default runner templates, the handoff schema, and every existing tasks file are
untouched. Every new behavior is reached only by adding a new, optional key.
