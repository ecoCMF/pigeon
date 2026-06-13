# PLAN — Pigeon for Coding Agents: the next level

**Authority:** `verdict` (Claude Opus 4.8) — session `roadmap`
**Date:** 2026-06-13
**Inputs reconciled:** `idea-gen-deepseek.md` (Lens B — Edit/Review),
`idea-gen-mimo.md` (Lens C — Reasoning Bank), `triage.md` (Sonnet gate),
`concord.md` (agy skeptic), `brainstorm/DESIGN.md` (Pillar 1 — Multi-Model Army,
already decided).

Every ruling below is grounded in the working tree, not the proposals'
paraphrases (file:line anchors verified). Where the gate and the skeptic
disagree, I RULE with a one-line reason, then synthesize one buildable roadmap.

---

## 0. Grounding facts (verified against source)

These five facts decide most of the contested rulings:

1. **The memory scope already indexes all distilled markdown.**
   `retrieval.py:116-118` — `scope="memory"` globs `config.memory_dir.rglob("*.md")`;
   `pack.py:86-87` — the pack *memory* layer queries exactly that scope. ⇒ Anything
   `distill` writes as markdown is **already retrievable and packable**. A parallel
   `strategies/*.json` tree and a 5th pack layer are redundant for recall.
2. **`distill` already renders outcomes and a decision ledger.**
   `distill.py:_render_session` (58-79) emits a `## Tasks` table (status, exit, duration,
   branch, skipped_because) and `## Decisions`; `_render_decisions` (99-125) regenerates
   the cross-session `decisions.md`. ⇒ The Reasoning Bank is ~80% built; the work is
   *enrichment*, not a new module.
3. **The worktree already captures a diffstat; full-diff is a ~5-line add.**
   `coordinate.py:940-942` — `_worktree_commit_and_remove` runs `git diff --stat HEAD~1 HEAD`.
   Writing the full diff to a `coordinate/diffs/` sibling under `config.root` lands on the
   **shared tree** (it survives `worktree remove`), sidestepping the worktree paradox
   (DESIGN §2d/§5).
4. **The graph already nodes decisions, artifacts and memory pages.**
   `graph.py:72-79` turns every handoff decision and `state.artifacts` pointer into a node;
   `distill.py:172-173` rebuilds the graph on every distill. ⇒ No dedicated "strategy node"
   type is needed; enriched memory + artifact pointers flow into the graph for free.
5. **The coordinator sees telemetry only after a child exits** (DESIGN Fact #4;
   `BudgetTracker`, `coordinate.py:951-977`; `by_agent_report`, 706-735). ⇒ Any
   *proactive* in-coordinator scoring/throttling that needs in-flight data is unenforceable;
   model scoring must be **offline and read-only**.

All commands + handoffs are built up-front in one pass and `--dry-run` prints exactly
those commands — the invariant nothing below may break.

---

## 1. Reconciliation — where the gate and skeptic disagree, I RULE

| # | Capability | Gate (Sonnet) | Skeptic (agy) | **VERDICT — one-line reason** |
|---|---|---|---|---|
| 1 | **Diff materialization** | keep, with `max_diff_kb` config | downscope — drop the config | **DOWNSCOPE (skeptic).** Ship the raw diff write; add caps only after a real large-diff failure is measured. |
| 2 | **Verdict re-entry loop** | keep, FLAG-1 fixed (pre-declared `reentry:`, bounded) | KILL — dynamic DAG breaks `--dry-run` | **ACCEPT the gate's fixed form, but defer to last and hard-gate it.** The skeptic's killer objection (on-the-fly task synthesis) is *removed* by FLAG-1: same task ID, statically visible, `max_reentry`-bounded, append-only honored. Build only if Phase D proves rework cycles frequent. |
| 3 | **Structured review schema** | keep `.review.json` + coordinator validate/merge | KILL — leaks agent reasoning into the scheduler | **SPLIT.** REJECT coordinator-side `validate_review()` + line-merging (violates DESIGN §1c, same rule that killed concordance voting). ACCEPT a thin *optional* review-artifact convention merged by the **concordance agent**, not coordinator code. |
| 4 | **Regression verification** | explicit tasks only (kill auto) | explicit tasks only (kill auto) | **CONVERGED — ACCEPT.** A normal task that runs tests, consumes the diff via `receives:`, writes results to `state.artifacts`. No auto-generation, no pytest hardcoding in the coordinator. |
| 5 | **Strategy extraction** | new `strategies/*.json` tree | downscope — markdown into `sessions/<sid>.md` | **DOWNSCOPE (skeptic).** Enrich the existing distill renderers (Fact #2); the memory scope recalls them for free (Fact #1). No parallel tree. |
| 6 | **Gate verdict ledger** | new `strategies/verdicts/*.json` | downscope — table in `decisions.md` | **DOWNSCOPE (skeptic).** `decisions.md` already carries every gate decision with provenance; make it query-friendly, don't fork it. |
| 7 | **Strategy-aware pack layer** | gate behind flag + measure | KILL | **DEFER behind measurement.** No 5th layer now — the memory layer already pulls distilled outcomes (Fact #1). Add a dedicated share *only if* Phase E proves memory-layer recall insufficient. |
| 8 | **Model tournament scoring** | keep — coordinator sorts pool by win-rate | KILL — starvation feedback loop | **RESOLVE: offline read-only report, never coordinator-side live re-sorting.** The skeptic is right that auto-demotion zeros a model on one transient 429 and starves it; the gate is right the data must feed back. Surface scores in `pigeon metrics`; a human/agent edits the pool in the tasks file. The loop runs through the filesystem, not hidden round-robin mutation. |
| 9 | **Strategy graph edges** | keep (sidecar provenance) | KILL — redundant | **KILL the dedicated nodes (skeptic).** Fact #4 — enriched memory + artifact pointers already populate the graph. No HTML-comment or sidecar injection. |
| 10 | **diff-shrink convergence** | drop it | drop it (semantically blind) | **CONVERGED — KILL.** `max_reentry` is the only stopping rule if re-entry is ever built. |

---

## 2. North star

Pigeon becomes the **shared nervous system for a fleet of coding agents that never
share a context window**: many models propose, review, and fix through a single
explicit DAG; every change moves as a *pointer* (a materialized diff, a review
artifact, a handoff) so a reviewer sees exactly what changed without re-reading the
repo; and every run's *outcome* — what passed, what a gate rejected and why, which
model won which kind of task — is distilled into committed, BM25-retrievable memory
that is automatically packed into the **next** run's context. The compounding gain
is twofold: agents stop re-deriving (the diff, the rejection, the winning approach
are already on disk as bounded slices), and the fleet stops repeating mistakes (last
session's rejected pattern and losing model are surfaced before this session acts).
It learns locally with **no LLM in the loop and no cloud** — pure filesystem,
deterministic distillation, and token accounting that *proves* each layer pays for
itself before it becomes a default.

---

## 3. Pillars

### Pillar 1 — Multi-Model Army *(decided in DESIGN.md; the substrate)*

- **Capability:** decouple *model* from *runner* so one runner template serves N
  models; named pools with deterministic round-robin; cross-wave pointer injection;
  clock-only throttle for free tiers.
- **Existing seam:** `coordinate.py` — `_build_command`/`_fill` (827-832) for the
  `{model}` placeholder, `load_tasks` (259-265) for round-robin, the scheduler ready
  point (~1329-1334) for `receives:` injection, telemetry emit for the per-model field.
- **Mechanism:** as fully specified in `DESIGN.md` §2 — `model:`/`model_pool:` task
  fields, `sha1(sid)`-seeded round-robin, deferred-write `receives:` injection into
  `state.artifacts`, per-pool `max_concurrency`/`min_spawn_interval_s`/`max_retries`,
  `deadline_minutes`. No macro, no strategy field, no schema bump.
- **Measurable win:** collapses the N×K runner explosion to one template; `--dry-run`
  still prints every command; per-`agent_run` `model` field unlocks Pillars 3–4.

### Pillar 2 — Edit · Review · Verify pipeline *(Lens B, downscoped)*

- **Capability:** every isolated change becomes a reviewable artifact; reviewers and
  verifiers consume it by pointer; fixes close the loop *only* under bounded, statically
  visible re-entry.
- **Existing seam:** `_worktree_commit_and_remove` (`coordinate.py:930-947`, already
  captures diffstat), `receives:` + `state.artifacts` (DESIGN §2d), `config.py` path
  properties (`runs_dir`/`events_dir`/`worktrees_dir` at 92-94 → add `diffs_dir`).
- **Mechanism:**
  - **2a Diff materialization** — after a changed worktree commit, write the full diff
    to `<contract>/coordinate/diffs/<run_id>/<task_id>.diff` (on the shared tree, Fact #3).
    No `max_diff_kb`, no fallback artifact. A downstream task `receives:` the `.diff`.
  - **2b Review-artifact convention** — a documented optional JSON/markdown shape a
    reviewer *may* emit and list in `state.artifacts`; the **concordance agent** reads
    and merges them. Shipped as a playbook (`skills.py` projection), **not** coordinator
    code. No `validate_review()`, no line-number merging in the scheduler.
  - **2c Verification task** — an ordinary task (`needs: [edit]`, `receives:` the diff)
    whose runner runs the suite and writes pass/fail to `state.artifacts`. Explicit in
    the tasks file; never auto-generated.
  - **2d Verdict re-entry (optional, hard-gated)** — a pre-declared `reentry: true`
    task the coordinator may re-queue (same ID, same template) up to `max_reentry` when
    an upstream verdict emits `decisions.verdict == "rework"` naming it; the only dynamic
    act is injecting the verdict's `state.artifacts` into that task's `receives:` for the
    re-run. `--dry-run` annotates it statically. No new IDs, no wave recomputation, no
    diff-shrink heuristic.
- **Measurable win:** a review/verify task's `kind: handoff` baseline stays near today's
  reduction_pct (it carries a diff *pointer*, not the diff); rework that previously needed
  a human relaunch completes inside one run, bounded and auditable.

### Pillar 3 — The Reasoning Bank *(Lens C, downscoped into distill + memory)* — **the heart**

- **Capability:** every run's outcome and every gate's ruling become committed,
  retrievable knowledge that the *next* run inherits with zero extra agent effort.
- **Existing seam:** `distill.py` (`_render_session`, `_render_decisions`, `_write_globals`),
  `retrieval.py` memory scope (116-118), `pack.py` memory layer (86-87), `tokens.py`
  accounting (`kind: distill`, `kind: pack`).
- **Mechanism:** **enrich the existing renderers — no new module, no parallel JSON tree.**
  - `_render_session` gains the resolved **`model`** per task (once Army Phase 1 lands)
    and a compact, tag-dense **"what worked"** line per task (status + model + tags
    tokenized from `doing`, same tokenizer as retrieval) so BM25 hits it.
  - `_render_decisions` formats gate accept/reject **reasons** into a query-friendly
    table in `decisions.md` (the data is already there at 110-112; make it dense).
  - Because both land as markdown under `config.memory_dir`, the memory scope recalls
    them via `pigeon retrieve` and the pack memory layer injects them — **for free**
    (Fact #1). The graph picks up the decisions/artifacts with no new code (Fact #4).
- **Measurable win:** see §4 (the learning loop) — proven via `kind: distill` overhead
  (<5%) and `kind: pack` net `saved_tokens` on outcome-aware bundles.

### Pillar 4 — Empirical Model Selection & Observability *(synthesized cross-pillar feedback)*

- **Capability:** measure which model wins which kind of task and surface it so the fleet
  routes better next time — **without** the coordinator silently re-ordering anything.
- **Existing seam:** `by_agent_report` (`coordinate.py:706-735`), `tokens.summarize`
  (`tokens.py:201-231`), the `model` telemetry field from Army Phase 1.
- **Mechanism:**
  - **4a Per-model metrics** — `by_agent_report` aggregates by resolved `model`, not just
    runner: tokens, duration, success rate, retry/deadline-skips. Ships with Army Phase 1.
  - **4b Offline model-score report** — `pigeon metrics --by-model` aggregates outcomes
    into a ranked, read-only view (win-rate by task-type, avg duration, n, with a
    `min_runs` floor so thin data shows "insufficient"). It is **diagnostic output a
    human or agent reads to edit the pool** in the tasks file — never an input the
    coordinator consumes to re-sort round-robin (Fact #5; resolves ruling #8).
- **Measurable win:** the pool improves through an auditable filesystem decision; the
  starvation feedback loop is structurally impossible because demotion is never automatic.

---

## 4. The learning loop (concrete)

The loop is **run → distill → recall → next run**, and every hop is already a real seam.

**What gets captured.** On each run, the manifest records per task: `status`, `exit_code`,
`duration_s`, `model` (Army P1), `isolation.branch`, `skipped_because`
(`coordinate.py` recorder). Each gate handoff carries `state.decisions` (accept/reject +
reason). Each producing task lists outputs in `state.artifacts` (now including its
materialized `.diff` and any review artifact).

**Where it lands.** `pigeon distill` (run automatically after a coordinate run via
`_write_globals` → `build_graph`) renders:
- `sessions/<sid>.md` — the enriched `## Tasks` table: *task · model · outcome · tags*.
- `decisions.md` — the enriched gate ledger: *decision · verdict · reason · provenance*.
- `graph.json` — decision/artifact/page nodes, rebuilt deterministically.
These live under `.pigeon/memory/` — **committed** (handoffs/runs are gitignored runtime
artifacts; the distilled record is what survives a clone).

**How it is recalled.** Two existing paths, zero new plumbing (Fact #1):
- `pigeon retrieve "<task or model or pattern>"` — BM25 over the memory scope hits the
  enriched tables.
- `pigeon pack "<next task>"` — the memory layer (20% of budget) injects the matching
  outcome rows into the bundle the next agent starts with. So a new task on a familiar
  shape opens with *"model X succeeded here, gate Z rejected pattern P because R."*

**How we prove it helped — via token accounting (`tokens.summarize`, `by_kind`):**
1. **Cost:** `kind: distill` `actual_tokens` must rise <5% from enrichment (the dense
   one-line-per-task format keeps it cheap).
2. **Benefit:** `kind: pack` `saved_tokens` on outcome-aware bundles vs. a baseline pack
   without the enriched memory. Net-positive ⇒ recall pays; net-negative ⇒ the data is
   noise and we stop (it stays retrieval-only).
3. **Routing:** Pillar 4 per-model `win_rate`/`avg_duration` trend over sessions — does
   pool editing informed by the report reduce duration and raise success?
4. **Decision point (Phase E gate):** if (2) is net-positive across ≥20 sessions, the
   loop compounds and we keep it as a default; if not, we drop the recall claim rather
   than ship a layer that hurts. The measurement *is* the gate.

---

## 5. Sequenced build plan (session-sized phases)

Dependency spine: Army P1/P2 → (P3 `receives:`) → Edit/Review/Verify; Reasoning Bank and
diff materialization start immediately in parallel. Army P4 (throttle) and P5 (topology)
are independent — land any time after P2 as need proves (DESIGN §4).

| Phase | Scope | Touches | Gate to next |
|---|---|---|---|
| **A** | **Army P1+P2** — `model:`/`{model}` (subbed only when resolved), preflight, default templates untouched, `model` in `agent_run` telemetry; `model_pools` + `sha1(sid)` round-robin | `config.py`, `coordinate.py` | Existing `test_coordinate.py` green; new tests: model substitutes only when resolved, no-model leaves template verbatim, deterministic pool assignment (same sid ⇒ same map). |
| **B** | **Diff materialization (2a)** + **Reasoning Bank core (3)** — distill enrichment (model + outcome + verdict-reason tables) + **per-model report (4a)**. *No Army dep beyond P1's model field.* | `coordinate.py` (`_worktree_commit_and_remove`, `by_agent_report`), `config.py` (`diffs_dir`), `distill.py` | `.diff` written on every changed worktree commit; `pigeon retrieve "<past task>"` surfaces the enriched row; `kind: distill` overhead <5%. |
| **C** | **Army P3** — `receives:` deferred-write cross-wave injection into `state.artifacts` | `coordinate.py` (run loop, scheduler) | Re-run *this* roadmap topology with `receives:` instead of the prompt-glob; `--dry-run` still prints all commands. |
| **D** | **Edit·Review·Verify (2b+2c)** — review-artifact convention as a playbook (agent-merged), explicit verification tasks consuming the diff | `skills.py` (playbook), tasks-file conventions; **no new coordinator parsing** | A review task reads the `.diff` pointer and emits an artifact in `state.artifacts`; the concordance agent merges; a verify task gates downstream via `needs:`. |
| **E** | **Empirical selection + recall proof (4b)** — offline `pigeon metrics --by-model` report; measure pack recall benefit | `tokens.py`/CLI report; **measurement only** | Report ranks models with a `min_runs` floor. **Decision:** keep outcome-aware recall iff `kind: pack` `saved_tokens` is net-positive over ≥20 sessions; else demote to retrieval-only. No coordinator pool re-sorting, ever. |
| **F** | *(optional, hard-gated)* **Verdict re-entry (2d, fixed form)** — pre-declared `reentry: true`, `max_reentry`-bounded, statically visible | `coordinate.py` (scheduler re-queue) | **Build only if** Phase D shows rework cycles frequent enough to justify; re-runs converge within `max_reentry`; `--dry-run` annotates re-entry-eligible tasks. Otherwise never built. |

Phases A–B are the immediate, independent wins. C unblocks D. E is a measurement gate that
decides whether the recall claim survives. F is the only place we add controlled dynamism,
and only on evidence.

---

## 6. Success metrics

Lean entirely on the existing token ledger (`tokens.summarize`, `by_kind`) + per-model
aggregation:

- **`kind: handoff` reduction_pct** holds near today as diff/review *pointers* enter
  `state.artifacts` (pointers-not-payloads must not regress).
- **`kind: distill`** `actual_tokens` increase <5% from enrichment.
- **`kind: pack`** net `saved_tokens` on outcome-aware bundles vs. baseline (the Phase E
  decision metric).
- **Per-model** tokens / duration / success-rate / retry+deadline-skips via the enriched
  `by_agent_report`; pool-editing improves the trend over sessions.
- **Recall hit-rate:** fraction of new tasks whose pack surfaces a relevant prior outcome.
- **Re-entry (if built):** convergence within `max_reentry`; count of human relaunches avoided.

### We are NOT building this

- **Coordinator-side live pool re-sorting by win-rate** — starvation feedback loop
  (ruling #8); scores are offline/advisory, acted on by humans/agents editing the tasks file.
- **On-the-fly task synthesis / mid-run wave recomputation** — breaks `--dry-run` and
  append-only (DESIGN Fact #2/#3; ruling #2).
- **`verify.auto` / auto-generated verify tasks** — macro expansion in disguise (FLAG-2).
- **Coordinator-side `validate_review()` + line-number finding-merge** — leaks agent
  reasoning into the scheduler (DESIGN §1c; ruling #3).
- **Parallel `strategies/*.json` tree, `strategies/verdicts/`, `model_scores.json` as a
  coordinator input** — duplicates `distill` + the memory scope (rulings #5, #6).
- **A 5th "strategies" pack layer** — until/unless Phase E proves the memory layer
  insufficient (ruling #7).
- **Dedicated strategy graph nodes + HTML-comment/sidecar provenance** — redundant with
  existing decision/artifact/page nodes (ruling #9).
- **diff-shrink / `min_shrink_ratio` convergence heuristic** — semantically blind (ruling #10).
- **`max_diff_kb` / LFS diff config** — until a real large-diff failure is measured (ruling #1).
- **Retrieval LRU cache** — stale-context hazard; local retrieval is already sub-ms (concord §3).
- **In-coordinator TPM/RPM ledger** — blind during parallel waves (DESIGN Fact #4 / §6).
- **`model://` scheme; handoff schema v1.2** — `repo://` + existing `state.artifacts` suffice;
  `git://` only if the deferred worktree lift (DESIGN §5) proves needed.

---

_Synthesized by `verdict` from two lenses, two gates, and the army design. Grounded in the
working tree. Hand off to the Coordinator for sequencing into sessions._
