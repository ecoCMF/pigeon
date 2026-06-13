# Design Proposal: First-Class Multi-Model "Army" Support for Pigeon

**Author:** gen-mimo (Mimo v2.5 Free)  
**Session:** army-design  
**Date:** 2026-06-13  

---

## Problem Statement

Today, pigeon's coordinate layer routes tasks to **runner CLIs** — each
runner is an argv template keyed by name (`claude`, `opencode`, `agy`,
etc.). A model is baked into the template:

```yaml
runners:
  oc-nemotron: [opencode, run, -m, opencode/nemotron-3-ultra-free, "{prompt}"]
  oc-deepseek: [opencode, run, -m, opencode/deepseek-v4-flash-free, "{prompt}"]
  oc-mimo:     [opencode, run, -m, opencode/mimo-v2.5-free, "{prompt}"]
```

This means every new model requires a new runner entry — a combinatorial
explosion when you want "same CLI, N models." The tasks file references
`runner: oc-mimo` instead of `runner: opencode, model: mimo-v2.5-free`,
which conflates "which program to run" with "which model to use."

Additionally:
- **Handoffs are built up-front** before downstream tasks exist, so Wave N
  cannot receive Wave N-1 outputs as pointers — the pointers don't exist yet.
- **Budget tracking uses `budget.usd`**, which is meaningless for free models.
  The real ceiling is rate limits + wall-clock time.
- **The army → gate → verdict pattern** (Wave 1: many models propose,
  Wave 2: gate + concordance review, Wave 3: verdict synthesizes) is
  hand-wired in the tasks file with no reusable primitives.

This proposal addresses all five gaps.

---

## (a) `model:` Task Field + `{model}` Placeholder

### Current State

`coordinate.py:856` builds the command by substituting placeholders in
the runner template:

```python
cmd = [_fill(arg, subs) for arg in ccfg["runners"][task["runner"]]]
```

The `subs` dict currently contains `handoff`, `root`, `task_id`, `sid`,
`prompt`. A model is indistinguishable from the runner itself.

### Proposal

Add `model` to the task schema and inject it into the substitution dict:

```yaml
tasks:
  - id: gen-mimo
    runner: opencode          # generic runner — one per CLI
    model: mimo-v2.5-free     # NEW: which model the CLI should use
    doing: draft the design
```

The runner template uses a `{model}` placeholder:

```yaml
runners:
  opencode: [opencode, run, -m, "{model}", "{prompt}"]
```

**Backward compatibility:** When `model:` is absent, `{model}` is
replaced with the empty string and the `-m` flag + empty arg are
stripped from the final command (a post-substitution cleanup pass).
Runners without `{model}` in their template are unaffected — the
placeholder simply isn't substituted.

### Why Not One-Runner-Per-Model?

One-runner-per-model works for 4 models. It breaks at 20. A model
registry (section (b)) plus a single CLI runner per CLI binary scales
linearly. The runner definition captures "how to invoke the CLI"; the
model captures "which backend to target." These are orthogonal concerns.

---

## (b) Named Model Pools + Round-Robin

### Current State

`config.py:101` defines `default_runner` — a string or list that
round-robins across *runners*. In `coordinate.py:259`:

```python
pool = ([default_runner] if isinstance(default_runner, str)
        else list(default_runner))
task["runner"] = pool[i % len(pool)]
```

This is runner-level round-robin. There is no model-level concept.

### Proposal

Add a `model_pools` section to config:

```yaml
coordinate:
  model_pools:
    free-gen:
      - opencode/nemotron-3-ultra-free
      - opencode/deepseek-v4-flash-free
      - opencode/mimo-v2.5-free
      - opencode/north-mini-code-free
    gated:
      - claude/sonnet
      - claude/opus
```

A task can reference a pool instead of a specific model:

```yaml
tasks:
  - id: gen-alpha
    runner: opencode
    model_pool: free-gen    # round-robin picks the next free model
    doing: draft proposal
```

**Resolution logic** (in `load_tasks`, after runner resolution):

```python
if task.get("model_pool"):
    pool = config.coordinate_cfg["model_pools"][task["model_pool"]]
    task["model"] = pool[i % len(pool)]  # i = index among unassigned tasks
```

The pool assignment is deterministic (task order in the YAML), so
re-running the same tasks file yields the same model assignments.

### Round-Robin Semantics

Round-robin spreads load across free providers to avoid rate-limit
walls. It is **task-order based**, not runtime load-based — a runtime
load balancer would require cross-process coordination that pigeon
deliberately avoids (the contract is the filesystem, not shared memory).

If a specific model is needed (e.g., Opus for the verdict), set `model:`
directly and skip the pool.

---

## (c) Army → Gate(Concordance) → Verdict Topology

### Current State

The `needs:` DAG already supports arbitrary topologies. The `crew:` block
already supports `verdict:` gates on subagents. But there are no
first-class primitives for the "many propose → gate → verdict" pattern —
it is hand-assembled in every tasks file.

### Proposal

Add a `topology:` field at the session level (alongside `sid` and `tasks`):

```yaml
sid: army-design
topology:
  army:
    propose: [gen-nemotron, gen-deepseek, gen-mimo, gen-north]
    gate: triage
    concordance: concord
    verdict: verdict
```

The coordinator validates this against the actual `needs:` DAG:
- All `propose` tasks must have no inter-dependencies (parallel wave).
- `gate` and `concordance` must `needs:` all `propose` tasks.
- `verdict` must `needs:` both `gate` and `concordance`.

This is **declarative validation**, not new execution logic — the DAG
scheduler (`compute_waves`) already handles the wave structure. The
`topology:` block is a semantic annotation that:
1. Enables `pigeon plan` to print a readable "army → gate → verdict" view.
2. Lets the coordinator auto-inject upstream artifact pointers into
   downstream handoffs (see section (d)).
3. Provides a schema for reusable patterns (a "design-brainstorm"
   topology could be a template).

### Extension: `crew:` Verdict Gates

The existing `crew.subagents[].verdict` field already gates subagent
output. For the army pattern, the gate task's crew can include:

```yaml
crew:
  subagents:
    - role: adversarial-reviewer
      skill: security-audit
      verdict: must approve before hand-back
```

No schema change needed — the existing mechanism handles it.

---

## (d) Wave N Receives Wave N-1 Outputs as POINTERS-NOT-PAYLOADS

### Current State (The Problem)

In `coordinate.py:1201-1244`, all handoffs are built in a single pass
**before any task launches**:

```python
commands: list[tuple[dict[str, Any], list[str], Path]] = []
for task in tasks:
    handoff = ho.build_handoff(...)
    path = ho.write_handoff(handoff, config)
    cmd = _build_command(task, config, handoff_ref, sid, ...)
    commands.append((task, cmd, log_path))
```

A Wave 2 task's handoff is written before Wave 1 tasks have produced
any output. The handoff cannot reference artifacts that don't exist yet.

### Proposal: Two-Phase Handoff Build

Split handoff generation into two phases:

**Phase 1 — Pre-launch** (before Wave 1 starts): Build handoffs for
Wave 1 tasks only. These need no upstream pointers.

**Phase 2 — Inter-wave** (after Wave N completes, before Wave N+1
starts): Build handoffs for Wave N+1 tasks, injecting pointers to
Wave N outputs.

The inter-wave phase:

1. After each wave completes, scan the task list for tasks whose
   `needs:` are all satisfied.
2. Collect output artifacts from completed upstream tasks:
   - Files written to the repo (from `artifacts:` declarations).
   - Handoffs recorded back to the Coordinator (from `pigeon handoff`).
   - Worktree branches + commits (from `isolation: worktree`).
3. Inject these as `repo://` pointers into the downstream task's handoff
   `state.artifacts` array.

**Concrete mechanism** — add an `upstream_artifacts` helper:

```python
def upstream_pointers(task: dict, completed: dict[str, dict]) -> list[str]:
    """Collect repo:// pointers from completed upstream tasks."""
    pointers = []
    for need in task.get("needs", []):
        info = completed.get(need, {})
        # explicit artifacts from the upstream task's declaration
        for art in info.get("artifacts", []):
            pointers.append(art)
        # handoff recorded back to Coordinator
        if info.get("return_handoff"):
            pointers.append(f"repo://{info['return_handoff']}")
        # worktree branch (committed code)
        if info.get("branch"):
            pointers.append(f"repo://.pigeon/coordinate/worktrees/")
    return pointers
```

This is called during Phase 2 handoff construction. The downstream
task's handoff includes:

```json
{
  "state": {
    "artifacts": [
      "repo://.pigeon/coordinate/brainstorm/proposal-gen-mimo.md",
      "repo://.pigeon/coordinate/brainstorm/proposal-gen-deepseek.md"
    ]
  }
}
```

The receiving agent resolves these pointers on demand — never receives
the file contents in the handoff itself.

### Why Not Static Declaration?

The tasks file *could* hardcode output paths:

```yaml
artifacts:
  - repo://.pigeon/coordinate/brainstorm/proposal-{task_id}.md
```

But this couples the tasks file to implementation details. The inter-wave
phase is more robust: it collects whatever the upstream tasks actually
produced, including handoffs and branch commits.

---

## (e) Telemetry + Rate-Limit Handling for Free Models

### Current State

`budget.usd` and `budget.tokens` are hard ceilings in `BudgetTracker`.
Free models report `$0.00` cost — the budget never binds them. The real
ceilings are:
- **Rate limits** (requests/minute, tokens/minute per provider).
- **Wall-clock time** (free models may be slow; the run should still
  complete within a deadline).
- **Concurrency** (free tiers often limit parallel requests).

### Proposal

#### 1. Rate-Limit Tracker

Add a `RateLimitTracker` alongside `BudgetTracker`:

```python
class RateLimitTracker:
    """Per-model rate-limit tracking for free providers."""
    
    def __init__(self, limits: dict[str, dict] | None = None):
        # limits: {"opencode/nemotron-3-ultra-free": {"rpm": 10, "tpm": 100000}}
        self.limits = limits or {}
        self._windows: dict[str, deque] = {}  # model -> timestamps
        self._lock = threading.Lock()
    
    def record(self, model: str) -> None:
        """Record a request timestamp for this model."""
        ...
    
    def throttled(self, model: str) -> str | None:
        """Return a reason string if the model is rate-limited, else None."""
        ...
```

Config:

```yaml
coordinate:
  rate_limits:
    opencode/nemotron-3-ultra-free:
      rpm: 10        # requests per minute
      tpm: 100000    # tokens per minute
    opencode/deepseek-v4-flash-free:
      rpm: 15
      tpm: 150000
```

When `throttled()` returns a reason, the scheduler **defers** that task
(pauses 30s, retries up to `max_retries`), then skips if still limited.

#### 2. Wall-Clock Deadline

Add a `deadline` field at the run level:

```yaml
coordinate:
  budget:
    deadline_minutes: 30   # hard wall-clock ceiling
```

Checked in the scheduler loop alongside `budget.exhausted()`. When
elapsed time exceeds the deadline, remaining tasks are skipped with
`skipped_because=["deadline exceeded"]`.

#### 3. Per-Model Telemetry

Extend the telemetry event schema with a `model` field:

```json
{
  "kind": "agent_run",
  "sid": "army-design",
  "task": "gen-mimo",
  "runner": "opencode",
  "model": "opencode/mimo-v2.5-free",
  "actual_tokens": 12345,
  "baseline_tokens": 0,
  "saved_tokens": 0,
  "duration_s": 45.2,
  "rate_limit_remaining": 8
}
```

This feeds `pigeon metrics` and `by_agent_report`, enabling visibility
into which free models are slow, rate-limited, or wasteful.

#### 4. `pigeon metrics` Extensions

- **Per-model aggregation:** tokens, duration, success rate, rate-limit
  hits per model.
- **Cost column shows $0.00 for free models** but adds a "rate-limit
  pressure" column.
- **Wall-clock contribution:** how much each model contributed to total
  run time (the real cost of free models).

---

## Implementation Plan

### Phase 1: `model:` Field + `{model}` Placeholder (Minimal)

**Files changed:** `config.py`, `coordinate.py`

1. Add `model` to the task schema in `load_tasks()` validation.
2. Add `{model}` to the `subs` dict in `_build_command()`.
3. Add post-substitution cleanup: strip `-m ""` when model is empty.
4. Update `preflight()` to validate `{model}` presence in templates
   that reference it.
5. Update `format_plan()` to display model info.

**Tests:** Existing `test_coordinate.py` tests pass unchanged (no
model field = no behavior change). Add tests for:
- Task with `model:` resolves `{model}` in template.
- Task without `model:` strips empty `-m ""`.
- Pool round-robin assigns models deterministically.

### Phase 2: Model Pools

**Files changed:** `config.py`, `coordinate.py`

1. Add `model_pools` to `default_config()`.
2. Add pool resolution in `load_tasks()` — after runner resolution,
   resolve `model_pool` to a specific `model`.
3. Update `preflight()` to validate pool names exist.
4. Update `by_agent_report()` to aggregate by model.

### Phase 3: Inter-Wave Handoff Build

**Files changed:** `coordinate.py`

1. Refactor `run_coordinate()` to build handoffs in waves, not all at
   once.
2. Add `upstream_pointers()` helper.
3. After each wave completes, build handoffs for the next wave with
   injected upstream pointers.
4. Update `RunRecorder` to track which wave each task belongs to.

### Phase 4: Rate-Limit + Deadline Handling

**Files changed:** `coordinate.py`, `config.py`

1. Add `RateLimitTracker` class.
2. Add `deadline_minutes` to budget config.
3. Integrate rate-limit checks into the scheduler loop.
4. Extend telemetry events with `model` field.
5. Update `by_agent_report()` and `pigeon metrics` for per-model views.

### Phase 5: Topology Declarations

**Files changed:** `config.py`, `coordinate.py`

1. Add `topology:` field to tasks file schema.
2. Add validation in `load_tasks()` — verify DAG matches topology.
3. Update `format_plan()` for army → gate → verdict display.
4. Add `pigeon plan --topology` for topology-specific views.

---

## Config Schema Additions

```yaml
coordinate:
  # Existing fields unchanged...

  # NEW: named model pools (section b)
  model_pools:
    <pool-name>:
      - <provider/model>  # round-robin order

  # NEW: rate limits for free models (section e)
  rate_limits:
    <provider/model>:
      rpm: <int>          # requests per minute
      tpm: <int>          # tokens per minute
      max_retries: 3      # retries before skip

  # EXTENDED: budget (section e)
  budget:
    tokens: null
    usd: null
    deadline_minutes: null  # NEW: hard wall-clock ceiling
```

## Task Schema Additions

```yaml
tasks:
  - id: <string>
    runner: <string>           # existing: CLI runner name
    model: <string>            # NEW: which model (section a)
    model_pool: <string>       # NEW: round-robin from pool (section b)
    doing: <string>            # existing
    # ... all existing fields unchanged ...
```

## Tasks File Top-Level Addition

```yaml
sid: <string>
topology:                     # NEW: semantic annotation (section c)
  army:
    propose: [<task-id>, ...]
    gate: <task-id>
    concordance: <task-id>
    verdict: <task-id>
tasks: [...]
```

---

## Backward Compatibility

- **No `model:` field** → existing behavior, `{model}` replaced with
  empty string and cleaned up.
- **No `model_pools:` in config** → pool resolution skipped.
- **No `rate_limits:` in config** → rate-limit tracking disabled.
- **No `deadline_minutes:`** → no wall-clock ceiling.
- **No `topology:` in tasks file** → no validation, no auto-injection.
- **No `{model}` in runner template** → template works as before.

All additions are opt-in. Existing tasks files and configs work unchanged.

---

## Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| Inter-wave handoff build adds latency between waves | Handoff build is fast (JSON write + token count); negligible vs. LLM execution |
| Rate-limit tracker adds threading complexity | Same pattern as `BudgetTracker` — proven, thread-safe, minimal |
| Model pool round-robin is static (not runtime-adaptive) | Deliberate: pigeon avoids cross-process state. Measure with `pigeon metrics`, adjust pool order manually |
| Free models may not emit telemetry | `_extract_telemetry` already handles missing usage gracefully — returns `None` |
| Topology validation rejects valid-but-unusual DAGs | Topology is advisory, not enforced — tasks file can always override with explicit `needs:` |

---

## Summary

This design adds first-class multi-model support to pigeon by:

1. **Separating model from runner** — `model:` field + `{model}` placeholder.
2. **Named model pools** — round-robin across free providers.
3. **Topology annotations** — reusable army → gate → verdict patterns.
4. **Inter-wave pointer injection** — Wave N gets Wave N-1 outputs as `repo://` pointers.
5. **Rate-limit + deadline telemetry** — real ceilings for free models.

All additions are backward-compatible and opt-in. The implementation
builds on existing primitives (`needs:` DAG, `crew:` verdict gates,
`BudgetTracker`, `_extract_telemetry`) rather than introducing new
execution models.

---

*Proposal written by gen-mimo (Mimo v2.5 Free) for the army-design session.*
