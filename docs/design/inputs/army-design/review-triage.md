# Gate Review: Triage
**Reviewer:** triage (Claude Sonnet) — session army-design  
**Date:** 2026-06-13  
**Proposals reviewed:** proposal-gen-deepseek.md, proposal-gen-mimo.md, proposal-gen-north.md

---

## Proposal: gen-deepseek

### Ideas to KEEP
- `model:` task field + `{model}` placeholder — cleanest decoupling of CLI from inference endpoint; backward-compatible when absent.
- Named `model_pools:` in config with `pool:<name>` syntax on tasks; round-robin by task order (deterministic, no cross-process coordination).
- `receives:` task field for post-execution artifact injection, resolved at spawn time after deps complete.
- **Supplement handoff** approach (append-only delta, original handoff immutable) — principled and safe under concurrent execution; respects the append-only log invariant.
- `gate: true` + `concordance:` task annotations with named strategies (`unanimous`, `majority`, `any`, `adjudicate`).
- Phase-sequenced implementation (5 phases, ~6 sessions) — correctly defers complexity.
- `army:` YAML macro as a Phase 4 item (not Phase 1), which is the right ordering.
- Rate-limit config at model level with `rate_limited: true` flag — avoids hardcoding provider names.

### Ideas to FLAG

**FLAG 1 — Post-substitution `-m ""` cleanup is fragile**  
Risk: stripping `["-m", ""]` from an argv list after substitution is a parsing hack that breaks if the user writes `-m={model}` or reorders args. A wrong strip silently produces an invalid command.  
Fix: runner templates should only include `-m "{model}"` when `model:` is set. Safer approach (also mentioned in the proposal): reject at preflight if `{model}` appears in a template but no task using that runner sets `model:`; OR use a separate fallback template per runner (`opencode_base` without `-m`, `opencode_model` with it).

**FLAG 2 — `{supplement_handoff}` as a prompt-level placeholder requires agent awareness**  
Risk: the receiving agent must know to read a second handoff file. If the prompt template doesn't mention `{supplement_handoff}` or the agent ignores it, upstream artifacts are silently dropped.  
Fix: rather than a new placeholder, inject the resolved `supplements` array directly into the *primary* handoff's `state.artifacts` field just before spawning. The handoff file is rewritten atomically at that moment (not during the initial batch build). The original batch-built file is the immutable template; the spawn-time write is a new file at a versioned path.

**FLAG 3 — Concordance `with: <task_id>` couples tasks to specific IDs**  
Risk: `concordance: {with: concord, strategy: majority}` hardwires a task name; extracting the topology to a reusable template becomes impossible without string munging.  
Fix: concordance should be expressed at the topology level (`topology.army.concordance: concord`) and resolved by the coordinator, not embedded in individual task definitions.

---

## Proposal: gen-mimo

### Ideas to KEEP
- **Two-phase handoff build** (Phase 1: pre-launch for Wave 1; Phase 2: inter-wave after Wave N, before Wave N+1) — more architecturally clean than supplement handoffs; eliminates the secondary-file complexity.
- `model_pool:` as a *separate* task field from `model:` — avoids ambiguity (`model: pool:name` vs. `model: opencode/deepseek-v4-flash-free` are indistinguishable types in YAML).
- `upstream_pointers()` helper collecting from `artifacts`, `return_handoff`, and worktree info.
- `RateLimitTracker` with `deque`-based sliding window + `threading.Lock` — mirrors `BudgetTracker` exactly; low-risk addition.
- `deadline_minutes` at budget level — orthogonal to rate limits, simple to enforce in the scheduler loop.
- `model` field in telemetry event schema — essential for `pigeon metrics` per-model aggregation.
- `topology:` as **declarative validation + display** only; no new execution logic — correctly identifies that the DAG already handles execution.
- Pool resolution is deterministic by task-definition order — no runtime state needed.

### Ideas to FLAG

**FLAG 1 — `upstream_pointers()` includes worktree branches as directory pointers**  
Risk: `f"repo://.pigeon/coordinate/worktrees/"` is a directory, not a file. The current resolver handles `repo://` paths as file pointers; passing a directory produces a resolver error or silent no-op.  
Fix: only include explicit `artifacts:` declarations and `return_handoff` paths. Worktree branches should be excluded until the resolver has directory support.

**FLAG 2 — Topology validation rejects valid DAGs**  
The proposal says topology validation is "advisory, not enforced" but also says `load_tasks()` should verify the DAG matches the topology. These contradict each other.  
Risk: a valid tasks file with a slightly non-standard DAG (e.g., one gen task depends on a setup step) could fail validation.  
Fix: make topology a pure annotation. Do not add rejection logic to `load_tasks()`; only use it for display (`pigeon plan`) and auto-injection. A mismatch warning is acceptable; a hard error is not.

**FLAG 3 — Phase 3 (inter-wave injection) depends on topology but Phase 5 adds topology**  
The proposal's phasing puts topology declarations last (Phase 5) but inter-wave injection (Phase 3) uses `needs:` topology to decide which upstream artifacts to collect. This is a hidden dependency.  
Fix: Phase 3 can work without a `topology:` block by deriving the wave structure from `needs:` (which `compute_waves()` already does). Make explicit: topology adds display/shorthand but is not required for injection.

---

## Proposal: gen-north

### Ideas to KEEP
- Confirms the same core direction as the other two proposals: `model:` field, `model_pools:`, cross-wave pointers, rate-limit tracking. Useful as consensus signal.
- Correctly identifies that `budget.usd` is meaningless for free models and that the real ceiling is rate limits + wall clock.
- Notes that pointer resolution should be lazy — consistent with pigeon's pointers-not-payloads principle.

### Ideas to FLAG

**FLAG 1 — `model://` URI scheme is undefined**  
`resolve_pointer()` returns `f"model://{model}/{pointer}"` — this scheme has no resolver in pigeon. The only supported schemes are `repo://`, `file://`, and `s3://` (flagged).  
Fix: use `repo://` exclusively. Remove the `model://` scheme.

**FLAG 2 — Runner templates add `--model {model}` to claude and agy**  
The proposal defaults claude's template to `["claude", "-p", "{prompt}", "--model", "{model}"]`. Claude CLI doesn't use `--model` in this form, and agy's flag is unverified. This breaks existing claude tasks.  
Fix: only add `{model}` to runners that verifiably support it (opencode with `-m`). Keep claude and agy templates unchanged.

**FLAG 3 — `can_consume()` has a logic bug**  
`self.hourly_tokens[self.clock.current_hour] -= self.clock.last_reset` subtracts a time value from a token counter. This is semantically wrong and would produce negative or nonsensical counts.  
Fix: use the deque-of-timestamps sliding-window pattern from gen-mimo's `RateLimitTracker`.

**FLAG 4 — `species: army` and `wave_count: 3` are vague, unexplained fields**  
These don't connect to any existing pigeon config structure and aren't defined elsewhere in the proposal.  
Fix: remove; use `topology:` annotation as proposed by gen-mimo, or the `army:` macro shorthand from gen-deepseek.

**FLAG 5 — "No model + multi-model setup: Fails" breaks backward compat**  
Treating a missing `model:` as an error when `default_runner` is a list would break all existing tasks files that use round-robin runners without explicit models.  
Fix: missing `model:` means `{model}` is not substituted; the task uses the runner template as-is. Only error if `{model}` is in the template AND `model:` is absent (preflight check).

**FLAG 6 — Per-model budget config requires all model names up front**  
`coordinate.budget.models["opencode/nemotron-3-ultra-free"]` requires pre-registering every model in the config. With large pools this becomes config explosion.  
Fix: attach rate limits to pool members or to the pool itself, not to individual model strings.

**FLAG 7 — 8-week timeline mismatches pigeon's release cadence**  
pigeon ships in sessions (0.1.x in single commits). A week-based timeline is irrelevant.  
Fix: use session-based estimates as gen-deepseek does.

---

## Proposal Ranking

| Rank | Proposal | Rationale |
|------|----------|-----------|
| **1** | **gen-deepseek** | Most complete across all five areas. Best backward-compat analysis. Supplement handoff is novel; FLAG 2 is addressable. Phase breakdown is correct. |
| **2** | **gen-mimo** | Two-phase handoff build is architecturally cleaner than supplements. `model_pool:` as a separate field is better schema design. Risks table is excellent. Slight phasing dependency issue (FLAG 3) is easy to resolve. |
| **3** | **gen-north** | Confirms consensus on direction but has concrete bugs (FLAG 3), an undefined URI scheme (FLAG 1), and backward-compat violations (FLAG 2, FLAG 5). Useful only as a corroborating voice. |

---

## Consensus Design Decisions to Carry Forward

1. **Decouple model from runner**: Add `model:` (concrete string) and `model_pool:` (pool name) as optional task fields. `{model}` expands in runner templates only when `model:` is set; otherwise the template is used as-is.

2. **Named model pools**: `model_pools:` dict in coordinate config; pool members are plain model strings; round-robin assignment by task-definition order (deterministic, no runtime state).

3. **Cross-wave artifact injection via two-phase handoff build** (prefer gen-mimo's mechanism): Build Wave 1 handoffs pre-launch; build Wave N+1 handoffs after Wave N completes, injecting `repo://` pointers to upstream artifacts from `artifacts:` declarations and `return_handoff` fields. Do NOT inject directory pointers.

4. **Rate-limit tracking**: `RateLimitTracker` class with sliding-window deque per model + `threading.Lock`. Config-driven (`rate_limited: true` flag on pool members or `rate_limits:` section). `deadline_minutes` at budget level for wall-clock ceiling. Retries on 429/503 up to `max_retries`.

5. **`topology:` as a pure annotation**: Declare `propose`, `gate`, `concordance`, `verdict` roles for display and auto-injection. Never reject a valid `needs:`-DAG based on topology mismatch; emit a warning at most.

6. **Telemetry extended with `model` field**: Every `agent_run` event carries the resolved model string; `pigeon metrics` aggregates by model.

7. **`army:` macro deferred to Phase 4**: Implement primitives (model field, pools, inter-wave injection, rate limits) first; the macro is syntactic sugar over primitives that should be validated in production use before hiding behind a shorthand.

8. **All additions opt-in**: No `model:`, no pools, no topology → existing tasks files run unchanged.

9. **Concordance at topology level, not task level**: Do not embed `concordance: {with: <id>}` in individual task definitions; express it in the `topology:` block.

10. **`repo://` only for pointers**: No new URI schemes. The `model://` scheme (gen-north) is rejected.
