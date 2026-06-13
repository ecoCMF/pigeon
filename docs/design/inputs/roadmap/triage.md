# TRIAGE — Roadmap Synthesis

**Gate:** triage (Claude Sonnet 4.6) | **Session:** roadmap | **Date:** 2026-06-13

**Inputs read:**
- `.pigeon/coordinate/roadmap/idea-gen-deepseek.md` — Lens B: Edit & Review Pipeline
- `.pigeon/coordinate/roadmap/idea-gen-mimo.md` — Lens C: Reasoning Bank
- `.pigeon/coordinate/brainstorm/DESIGN.md` — Pillar 1, already decided (Multi-Model Army)

---

## 0. Already Decided: Pillar 1

**Pillar 1 — Multi-Model Army** is fully specified in DESIGN.md and is not reconsidered here.
Six phases: `model:` field + `{model}` seam (§2a), named pools + round-robin (§2b), topology
annotation (§2c), cross-wave `receives:` injection (§2d), free-pool throttle + deadline + retry
(§2e), deferred `git://` resolver (§5).

Army Phase 1-2 (`model` field, pools) are prerequisites for anything that reads `model` from
run manifests. Army Phase 3 (`receives:`) is the scheduler seam that the Edit-Review-Fix pipeline
depends on.

---

## 1. Candidate Pillars

### Pillar 2 — Edit-Review-Fix Pipeline (Lens B)

**Core seam:** `_worktree_commit_and_remove` (`coordinate.py:930-947`) and `receives:` (DESIGN
§2d Phase 3).

Capabilities in dependency order:

| ID | Capability | Seam | Unlocks |
|----|-----------|------|---------|
| 2a | **Diff materialization** — write full diff to `coordinate/diffs/<run>/<task>.diff` after every worktree commit | `_worktree_commit_and_remove` + one new `Config` property; ~20 lines | Bounded pointer-safe diff artifact; input to 2b, 2c, 2d |
| 2b | **Structured review schema** — `.pigeon/reviews/<sid>/<task>.review.json` validated against a new `review.schema.json`; concordance merges findings by target + line range | Mirrors `validate_handoff()`; `repo://` pointers; `state.artifacts` | Machine-parseable findings; structured input for verdict |
| 2c | **Regression verification task** — explicit task runs test suite against producer's worktree branch vs. HEAD, writes `.review.json`-compatible verdict | `isolation: worktree` branch; diff artifact (2a); DESIGN §2e `deadline_minutes` | Hard test evidence into verdict gate |
| 2d | **Verdict re-entry** — bounded re-queue of a pre-declared `reentry: true` producer task when verdict emits `decisions.verdict == "rework"` | `receives:` (§2d); handoff `state.decisions`; DAG scheduler | Closes the rule-and-apply loop without human intervention |

**Keep all four.** 2a is the cheapest highest-leverage change in either proposal. 2b decouples
review production from consumption. 2c supplies hard evidence to the verdict. 2d closes the loop.

**Phase gate:** 2a can ship immediately (no Army deps). 2b-2d require Army Phase 3 (`receives:`).

See §2 for the re-entry and auto-generation flags.

---

### Pillar 3 — Reasoning Bank (Lens C)

**Core seam:** `coordinate/runs/<sid>-<n>.json` run manifests and `distill.py:_distill_one`.

Capabilities in dependency order:

| ID | Capability | Seam | Unlocks |
|----|-----------|------|---------|
| 3a | **Strategy extraction** — new `strategies.py`; `extract_strategies()` mines per-task outcome records from run manifests into `.pigeon/memory/strategies/<sid>-<tid>.json` | `distill.py` post-pass; existing manifest fields; no LLM | Foundation for all below |
| 3b | **Gate verdict ledger** — `extract_gate_verdicts()` emits accept/reject/reason records from `state.decisions` in gate handoffs | Handoff `state.decisions`; strategy record format | Past rejections retrievable before next proposal |
| 3c | **Strategy retrieval** — `strategies.py:build_strategy_index()` renders dense `index.md` + per-record `.md` summaries; BM25 hits them via `pigeon retrieve` (already indexes `memory/`) | `retrieval.py`; existing BM25 index scope | Zero-cost recall via existing tooling |
| 3d | **Strategy-aware pack layer** — 5th layer in `pack.py:_LAYERS` at 10% budget | `retrieval.query()` over memory scope; 3c must exist | Automatic institutional memory in every context bundle |
| 3e | **Model tournament scoring** — `score_models()` aggregates 3a records; `load_tasks` sorts pool by descending `win_rate` before `sha1(sid)` offset | Army Phase 2 (`model:` in run manifests); `coordinate.py:load_tasks` | Empirical, LLM-free model selection feeding back into Pillar 1 |

**Keep 3a-3c as the minimum viable loop.** Ship immediately — no Army deps.

**Keep 3e.** Highest cross-pillar leverage: observed outcomes reshape pool ordering.
Requires Army Phase 2. Backward-compatible: absent scores, plain round-robin is unchanged.

See §2 for the pack-layer budget flag (3d) and the fragile provenance mechanism (Cap 5).

**Phase gate:** 3a-3c: no dependencies, start now. 3e: requires Army Phase 2. 3d: requires 3c
plus empirical validation (see FLAG-3).

---

### Pillar 4 — Observability & Graph Provenance (synthesized)

Neither lens proposed this as a pillar; it emerges from cross-cutting concerns in both.

| ID | Capability | Source | Seam |
|----|-----------|--------|------|
| 4a | **Per-model metrics** — `model` field in `agent_run` events; `by_agent_report` gains per-model aggregation | DESIGN §2e (already accepted) | Ships with Army Phase 1 |
| 4b | **Strategy graph nodes** — `graph.py:build_graph` gains strategy scan pass; `session→produced→strategy`, `model→associated→strategy` edges | Lens C Cap 5 | Requires Pillar 3 Phase 1 |
| 4c | **Review artifact nodes** — `.review.json` files indexed as artifact nodes; `strategy→influenced→session` via structured sidecar (see FLAG-4) | Lens B Cap 3 | Requires Pillar 2 Cap 2b |
| 4d | **Re-entry audit trail** — each re-entry round writes a new handoff; graph captures the chain via existing `references` edges | Lens B Cap 2 | Requires Pillar 2d |

**Keep 4a-4b.** Both are additive, backward-compatible additions to existing modules.
4c and 4d are low-risk extensions that become available once the upstream pillars land.

---

## 2. Flags: Speculative / Infeasible / Ethos-Violating

### FLAG-1 — ETHOS VIOLATION: On-the-fly task generation in the re-entry loop (Lens B, Cap 2)

**Severity: BLOCK before implementation.**

Deepseek proposes that after the scheduler loop the coordinator "builds a new task on the fly
(not in the original tasks file), re-computes waves, and re-runs." This is the same hidden
dynamism that killed `army:` macro expansion (DESIGN §6, C-c-2): tasks that appear nowhere in
the tasks file cannot be printed by `--dry-run`, breaking the up-front build invariant at
`coordinate.py:1203-1244`. Additionally, synthesizing a new task ID (`fix-<original_id>-v<n>`)
invents a handoff claim that `write_handoff` was never told about — either skipping the claim
(violating append-only, Fact #2) or requiring the pre-accounting pass to be re-run mid-schedule
(breaking the up-front contract).

**Fix (keep the intent, restore the ethos):** Add a `reentry: true` flag to a task definition.
This pre-declares that the coordinator may re-queue this exact task (same ID, same runner, same
template) up to `max_reentry` times if an upstream verdict task emits
`state.decisions.verdict == "rework"` and names this task's ID. The only dynamic behavior is: on
re-queue, the scheduler injects the verdict's `state.artifacts` into this task's `receives:` for
that iteration (using the deferred-write mechanism from DESIGN §2d). The task ID, runner, and
prompt template are unchanged. `--dry-run` can annotate the task statically as "re-entry eligible
(up to N times)." No new task IDs, no mid-run wave recomputation, no hidden handoffs.

**Also kill:** The diff-shrink convergence heuristic (`min_shrink_ratio`). Diff size does not
correlate with fix quality. Use `max_reentry` alone; let the verdict agent decide when to stop
issuing `rework` decisions.

---

### FLAG-2 — ETHOS VIOLATION: `verify.auto: true` (Lens B, Cap 4)

**Severity: BLOCK before implementation.**

Auto-generating a `verify-<producer_id>` task for every non-readonly worktree task is macro
expansion in a new costume. DESIGN §6 rejected `army:` macro expansion with C-c-2: "A 1-line
block exploding into hidden tasks makes `--dry-run` un-auditable." Auto-verification tasks are
identically invisible at plan time.

**Fix:** The `coordinate.verify` config block is kept as a *template* — it specifies the test
command, deadline, and runner for verification tasks. Verification tasks are written explicitly
in the tasks file (e.g., `id: verify-edit`, `runner: <verify-runner>`, `needs: [edit-task]`,
`receives: ["repo://.pigeon/coordinate/diffs/..."]`). A future task-emitter that writes an
inspectable expanded tasks file (hinted at DESIGN §2c) could auto-generate them; runtime
auto-generation is not that.

---

### FLAG-3 — SPECULATIVE: Strategy-aware pack layer (Lens C, Cap 4 / Pillar 3d)

**Severity: WATCH — do not block, gate behind a flag.**

10% of ~4000 pack tokens ≈ 400 tokens fits only ~3-5 strategy summaries. If BM25 recall
precision on strategy records is low, agents receive noise rather than signal — worse than the
status quo. This is untested across real sessions.

**Fix:** Gate the strategies pack layer behind `coordinate.reasoning_bank: true` (opt-in per
project). Require a minimum of ≥ 10 strategy records before activating. Measure BM25 recall
precision across ≥ 20 sessions before making it a default layer. If precision < 0.6 at that
point, kill the pack layer and leave strategies as retrieval-only (3c is still valuable without
3d).

---

### FLAG-4 — FRAGILE: HTML comment provenance injection (Lens C, Cap 5)

**Severity: LOW — kill the mechanism, keep the goal.**

Injecting strategy IDs into Markdown pack files as `<!-- strategies: strat-x, strat-y -->`
and parsing them back in `build_graph` is brittle: it couples the provenance system to pack
file format internals, breaks silently on any pack renderer change, and is invisible to all
tooling except the graph scanner.

**Fix:** When `pack.py` injects the strategies layer, write strategy IDs to a structured sidecar
at `.pigeon/coordinate/packs/<bundle>.strategies.json`. `build_graph` reads the sidecar for
`applied_to` edges. No comment parsing, no format coupling. If the sidecar is absent, the
`applied_to` edge simply doesn't exist — backward compatible.

---

## 3. Hard Dependencies Between Pillars

```
Pillar 1 — Army
  ├─ Phase 1 (model field + telemetry)  ──────────────────► Pillar 4a (per-model metrics, same session)
  ├─ Phase 2 (pools + round-robin)      ──────────────────► Pillar 3e (scores inform pool order)
  └─ Phase 3 (receives:)                ──────────────────► Pillar 2b / 2c / 2d (all need cross-wave injection)
                                                             Pillar 2a can start without this

Pillar 3 — Reasoning Bank
  ├─ 3a-3c (extraction, ledger, index)  ── no deps ───────► start immediately
  ├─ 3d (pack layer)                    ── needs ─────────► 3c + 20-session validation
  └─ 3e (model scoring)                 ── needs ─────────► Army Phase 2 (model in manifests)

Pillar 2 — Edit-Review-Fix
  ├─ 2a (diff materialization)          ── no deps ───────► start immediately (20-line change)
  ├─ 2b (review schema)                 ── needs ─────────► Army Phase 3 (receives:) + 2a
  ├─ 2c (regression gate)               ── needs ─────────► Army Phase 3 + 2a (explicit tasks only)
  └─ 2d (re-entry, fixed form)          ── needs ─────────► Army Phase 3 + 2a + FLAG-1 fix agreed

Pillar 4 — Observability
  ├─ 4a                                 ── ships with Army Phase 1
  ├─ 4b (strategy graph)                ── needs ─────────► Pillar 3 Phase 1 (3a)
  └─ 4c (review artifact graph)         ── needs ─────────► Pillar 2 Cap 2b
```

**Non-obvious cross-pillar feedback loop:** Pillar 3e (model tournament) feeds back into Pillar 1
pool ordering. This is the highest-value cross-pillar seam: Army assigns tasks to models;
Reasoning Bank measures which models win; scores reshape pool order. Guard: the DESIGN §2b
"no scores → plain round-robin" fallback must hold unconditionally. Stale scores (< 10 runs)
must not corrupt pool ordering — apply the scoring sort only above a `min_runs` threshold.

---

## 4. Single Biggest Risk

**Dynamic task generation in the verdict re-entry loop (FLAG-1).**

Not merely an ethos concern — a correctness risk. On-the-fly task creation mid-run breaks the
up-front command build (`coordinate.py:1203-1244`), silences `--dry-run` for those tasks, and
requires either violating append-only handoffs or re-running the pre-accounting pass mid-schedule.
The fix (pre-declared `reentry: true`, bounded re-queue of the same definition) preserves
correctness while retaining the "verdict agent closes the loop" intent.

The risk is amplified because the re-entry loop is the most compelling capability in either
proposal — it will attract implementation enthusiasm before the guardrails are in place. The
fix must be agreed with the Coordinator **before** anyone opens `coordinate.py`.

---

## 5. Proposed Session Build Order

| Session | Deliverable | Deps |
|---------|-------------|------|
| 1 | Army Phase 1-2 (DESIGN already specified) | — |
| 2 | Pillar 3a-3c (strategy extraction, gate ledger, index) + Pillar 2a (diff materialization) | None — both start immediately |
| 3 | Army Phase 3 (`receives:`) | Army Phase 1-2 |
| 4 | Pillar 2b (review schema) + Pillar 2c (explicit verification tasks) + Pillar 3e (model scoring) | Session 2 + Session 3 |
| 5 | Pillar 2d (re-entry, FLAG-1 fix agreed) + Pillar 4b-4c (graph extensions) | Session 4 |
| 6 | Pillar 3d (pack layer, only if precision validated) + Pillar 4d (re-entry audit) | Session 5 + 20-session data |
