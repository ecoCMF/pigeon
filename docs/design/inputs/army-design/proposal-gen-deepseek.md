# Proposal: First-Class Multi-Model ("Army") Support for Pigeon

## Problem

Today pigeon coordinates agent **CLIs** (claude, agy, opencode) but has no
first-class concept of a **model**.  The army-design-1 run works around this
with per-model runner entries:

```yaml
runners:
  oc-nemotron: [opencode, run, -m, opencode/nemotron-3-ultra-free, "{prompt}"]
  oc-deepseek: [opencode, run, -m, opencode/deepseek-v4-flash-free, "{prompt}"]
  oc-mimo:     [opencode, run, -m, opencode/mimo-v2.5-free, "{prompt}"]
  oc-north:    [opencode, run, -m, opencode/north-mini-code-free, "{prompt}"]
```

This works but doesn't scale: N models × K runners = N×K config entries.
Worse, there is no pooling, no round-robin *within a model tier*, no
rate-limit awareness, and no way to switch models without editing config.

---

## (a) `model:` task field + `{model}` placeholder

**Proposal: add a `model:` field to tasks and a `{model}` placeholder.**

### Task-level field

```yaml
tasks:
  - id: gen-feature
    runner: opencode        # the CLI binary to invoke
    model: opencode/deepseek-v4-flash-free   # which model it uses
```

- `model:` is **optional**. When absent, the runner template applies as-is
  (backward-compatible).
- When present, `{model}` in the runner's argv template expands to its value.
- `{model}` is undefined (literal) in templates that don't use it —
  runners that take `-m` add it; runners that don't, leave it out.

### Runner templates — updated defaults

```yaml
runners:
  opencode: [opencode, run, -m, "{model}", "{prompt}"]
  claude:   [claude, -p, "{prompt}"]           # no {model}; Claude is itself
  agy:      [agy, -p, "{prompt}"]
```

An opencode task with `model: opencode/nemotron-3-ultra-free` produces:
`[opencode, run, -m, opencode/nemotron-3-ultra-free, "prompt..."]`

A claude task with no `model:` produces `[claude, -p, "prompt..."]` —
backward-compatible, no change.

### Why NOT one-runner-per-model

The army-design-1 workaround (one runner per model) looks clean but fails at:

- **N×K explosion**: 3 runners × 12 models = 36 config entries.
- **No pooling**: `default_runner: [oc-a, oc-b, oc-c]` round-robins *runner
  names*, not models. You cannot say "spread these 8 tasks across any 3 free
  opencode models."
- **No late binding**: The model is baked into the runner template; there is
  no way to select a model at task-definition time without writing a new
  runner.

The `model:` field decouples **CLI** (the binary) from **model** (which LLM
it talks to). This is the right seam — an opencode task with a different
`model:` is the same CLI, different inference endpoint.

---

## (b) Named model pools + round-robin across free providers

**Proposal: `model_pools` in config — named sets of models, assignable via
`model: pool:<name>` or `model: <provider/model>`.**

### Config schema

```yaml
coordinate:
  model_pools:
    free-opencode:
      - opencode/nemotron-3-ultra-free
      - opencode/deepseek-v4-flash-free
      - opencode/mimo-v2.5-free
      - opencode/north-mini-code-free
    pro-opencode:
      - opencode/gpt-4o
    sonnet:
      - anthropic/claude-sonnet-4-20250514
```

### Task-level assignment (three modes)

```yaml
# 1. Explicit model — always the same model for this task
- id: gen-feature
  runner: opencode
  model: opencode/deepseek-v4-flash-free

# 2. Pool reference — round-robin across pool members at spawn time
- id: gen-brainstorm
  runner: opencode
  model: pool:free-opencode

# 3. Pool + round-robin via default_runner with a pool
  # coordinate.default_runner = "free-opencode" would assign every
  # unassigned task a random model from the pool
```

### Round-robin mechanics

When `model:` is a pool (e.g. `pool:free-opencode`):

1. `load_tasks` resolves `pool:free-opencode` → `["opencode/nemotron-3-ultra-free", ...]`.
2. Round-robin index is per-task, same algorithm as `default_runner` list
   today (`coordinate.py:259-265`): `pool[i % len(pool)]` by task order.
3. The resolved model is written into the handoff + run manifest so the
   receiving agent knows which model it ran on.
4. Each pool member is a model string, **not a runner name** — the runner is
   still declared independently (`runner: opencode`), so model and CLI are
   decoupled.

### Backward compatibility

- No `model_pools:` config → pools aren't used. Task `model:` is a single
  string; `{model}` placeholder works as described.
- No `model:` field on a task → `{model}` in the runner template stays
  literal. Existing runners (most of which don't use `{model}`) are
  unaffected.

---

## (c) Army → gate(concordance) → verdict topology

The army-design-1 run already demonstrates the topology using `needs:`:

```
Wave 1:  [gen-nemotron, gen-deepseek, gen-mimo, gen-north]    (army — parallel)
Wave 2:  [triage, concord]                                      (gate + concordance — parallel)
Wave 3:  [verdict]                                               (reconciliation — after both gates)
```

### What exists today

- `needs:` is a full DAG scheduler — tasks launch when all deps exit 0,
  downstream tasks are skipped on failure (`coordinate.py:1292-1374`).
- `crew:` gives each task a deterministic subagent roster with `verdict`
  gates — but this is prompt-level, not enforced by the coordination layer.

### What the army topology adds

**Formalize the gate pattern as a first-class concept:**

```yaml
- id: triage
  runner: sonnet
  gate: true              # NEW: this task is an "army gate"
  needs: [gen-nemotron, gen-deepseek, gen-mimo, gen-north]
  concordance:
    with: concord         # NEW: concordance reviewer must also approve
    strategy: majority    # majority | unanimous | any | adjudicate
```

- `gate: true` marks a task as a gate — its output is a **review** that
  downstream tasks (the verdict) must reconcile.
- `concordance` links a parallel reviewer: both must complete before the
  downstream task runs. The `strategy` determines how disagreements are
  resolved:
  - `unanimous` — both must agree; disagreement → escalation handoff.
  - `majority` — 2+ reviewers agree (minimum for 3+ reviewers).
  - `any` — first gate to finish unblocks.
  - `adjudicate` — always passes through to verdict (what army-design-1
    does: triage + concord both produce independent reviews, verdict
    reconciles).

### The 3-wave topology as a config shorthand

```yaml
army:
  model_pool: free-opencode
  size: 4                              # spawn 4 gen tasks, round-robin over pool
  gate:
    runner: sonnet
    model: pool:sonnet                 # or explicit model
    concordance:
      runner: agy
  verdict:
    runner: claude
    model: anthropic/claude-opus-4-8-20250514
```

This expands to the full 7-task YAML at plan time — a macro over the
existing `needs:` DAG.

---

## (d) Wave N receives Wave N-1 outputs — pointers, not payloads

### The problem

Today `handoff.py` builds every handoff **before** any task runs
(`coordinate.py:1212-1228`). The handoff carries `artifacts:` — but these are
declared by the operator in the tasks file, not produced by upstream tasks.
There is no way for a Wave N task to say "give me the output of that Wave
N-1 task" because that output doesn't exist yet.

In army-design-1 the workaround is a glob pattern in the prompt:
```
Read every file matching .pigeon/coordinate/brainstorm/proposal-*.md.
```
This is fragile — the agent has to discover the files itself.

### Proposal: post-execution artifact injection

Add a new `receives:` field to tasks that is resolved **at spawn time**
(after upstream tasks complete), not at handoff-build time:

```yaml
- id: triage
  runner: sonnet
  needs: [gen-nemotron, gen-deepseek, gen-mimo, gen-north]
  receives:                             # NEW: injected after deps finish
    artifacts:
      - "repo://.pigeon/coordinate/brainstorm/proposal-{task_id}.md"
    pattern: "repo://.pigeon/coordinate/brainstorm/proposal-*.md"
```

**Resolution timing change:**

1. Pre-run: build handoffs for *all* tasks (today's model). The triage
   handoff's `artifacts` is empty for the Wave N-1 outputs.
2. When triage's `needs` are met (all gen tasks completed), inject the
   upstream artifacts into the handoff **before spawning**:
   - Resolve `recieves.artifacts` — glob against the filesystem (which now
     has the upstream outputs).
   - Write a supplementary handoff with the resolved artifact list.
   - Append `supplement: <path>` to the original handoff; the receiving
     agent reads both.

This is **pointers-not-payloads**: resolved `repo://` URIs pointing to files
that exist on disk. No inline content in the handoff JSON.

### Handoff supplement schema

```json
{
  "schema_version": "1.2",
  "sid": "army-design",
  "from": "Coordinator",
  "to": "triage",
  "supplements": [
    "repo://.pigeon/coordinate/brainstorm/proposal-gen-nemotron.md",
    "repo://.pigeon/coordinate/brainstorm/proposal-gen-deepseek.md",
    "repo://.pigeon/coordinate/brainstorm/proposal-gen-mimo.md",
    "repo://.pigeon/coordinate/brainstorm/proposal-gen-north.md"
  ],
  "state": {
    "done": [],
    "doing": "Gate the army's proposals..."
  }
}
```

The supplement handoff is written **just before spawning** the downstream
task. The runner's prompt template gets `{supplement_handoff}` which points
to this new handoff file. The agent reads both the original handoff (crew
assignments, constraints) and the supplement (pointers to upstream
artifacts).

### Why not rebuild the entire handoff?

Rebuilding would require re-serializing the entire handoff (including crew,
constraints, etc.) and atomically replacing it — risky during concurrent
execution. A supplement is append-only: the original handoff is immutable,
the supplement is a delta.

---

## (e) Telemetry + rate-limit handling for free models

### The budget.usd problem

Today `BudgetTracker` enforces `max_tokens` and `max_usd`. Free models cost
$0 — `max_usd` is meaningless. The real ceilings for free models are:

1. **Rate limits** — requests/minute, tokens/minute, requests/day.
2. **Wall-clock time** — the coordinate run has a deadline (the operator
   expects results within N minutes).

### Proposal: free-model budget mode

Extend the budget config to support a `free` ceiling:

```yaml
coordinate:
  budget:
    tokens: 500_000          # shared across the whole run
    usd: 10.00               # binds paid models
    free:                    # NEW: binds $0 models
      rate_per_minute: 30    # max requests/minute per model
      tokens_per_minute: 200_000
      wall_clock: 900        # max seconds the free phase can run
      max_retries: 3         # retry on 429/503 before failing task
```

### Rate-limit-aware scheduling

When a task uses a free model (detected by `model:` matching a free provider
or via explicit `rate_limited: true` on the model pool):

1. Before spawning, check the rate-limit ledger (an in-memory sliding window
   per model). If over limit, defer the task.
2. On 429/503 in the child process output, consider it a transient failure;
   increment a retry counter and reschedule (up to `max_retries`).
3. Track wall-clock across all free-model tasks. Once `wall_clock` seconds
   have elapsed since the first free task started, skip remaining queued
   free tasks (they contribute to `budget.free.wall_clock`, not `usd`).

### Detection mechanism

Free-model detection needs a config-level signal — pigeon cannot reliably
infer pricing from a model string. Options:

```yaml
coordinate:
  model_pools:
    free-opencode:
      models:
        - name: opencode/nemotron-3-ultra-free
          rate_limited: true
          rpm: 30          # requests per minute
          tpm: 200_000     # tokens per minute (optional override)
```

Paid models (default) are never rate-throttled by pigeon — the operator's
API key handles that. Only models flagged `rate_limited: true` enter the
free-model scheduler.

### Interaction with wave scheduling

The wave scheduler already respects `needs:`. Rate-limit deferral adds a
**second axis** within a wave: tasks on the same free model are rate-gated
even though they are topologically ready. This naturally smooths the request
profile without requiring the operator to pre-compute stagger timings.

---

## Implementation phases

### Phase 1: `{model}` placeholder + `model:` field (minimal, ~1 session)

- Add `model` to the task schema (optional string).
- Add `{model}` to the `_fill()` subs dict in `_build_command()`.
- Update opencode default runner template to `[opencode, run, -m, "{model}", "{prompt}"]` - but only when `model` is set; keep backward compat with a fallback template for `{model}`-less usage. (Simpler: require `model:` if `{model}` is in the template, else the literal `{model}` is passed to the CLI which errors. Safer: **only expand `{model}` if `model:` is set** — leave it un-substituted otherwise.)

### Phase 2: Named model pools + round-robin (~1 session)

- `model_pools` config section (dict of named lists).
- `load_tasks` resolves `pool:<name>` and `model:` at validation time.
- Round-robin index for pool members (same algorithm as default_runner list).
- Pool member written into run manifest and handoff for audit.

### Phase 3: Post-execution artifact injection (supplement handoffs, ~2 sessions)

- `receives` task field with `artifacts` (list of glob patterns).
- After upstream tasks complete (in the scheduler's ready-detection loop,
  `coordinate.py:1329-1334`), resolve `receives` patterns, write a
  supplement handoff, append `{supplement_handoff}` to the sub dict.
- Handoff schema v1.2 with `supplements` array.

### Phase 4: Army config macro (~1 session)

- `army:` top-level shorthand in tasks files (expands to N gen + gate +
  concordance + verdict).
- Concordance strategies and verdict reconciliation hints.
- The `army` → `gate` → `verdict` topology becomes a first-class template.

### Phase 5: Rate-limit-aware scheduling (~1 session)

- Model-level rate limit config (`rpm`, `tpm`, `wall_clock`, `max_retries`).
- In-memory sliding-window ledger per model.
- Deferred task queue with rate-gated release.
- 429/503 retry logic (detection from child output).

---

## Backward compatibility summary

| Feature | Existing runs unaffected? |
|---|---|
| `model:` on tasks | Yes — absent = no expansion of `{model}` |
| `model_pools:` in config | Yes — absent = no pool resolution |
| `receives:` on tasks | Yes — absent = no supplement handoff |
| `army:` shorthand | Yes — absent = no expansion |
| Rate limits | Yes — absent = no deferral |
| Budget `free:` | Yes — `max_usd` still works for paid |
| `{model}` in runner template | Yes — only substituted when `model:` is set |
