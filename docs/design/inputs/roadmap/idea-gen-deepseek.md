# Lens B — Editing & Review: design proposal

**Author:** gen-deepseek
**Session:** roadmap
**Grounding:** DESIGN.md (army verdict), `coordinate.py`, `handoff.py`,
`resolve.py` (`pigeon retrieve "crew verdict gate worktree isolation review"`)

## The lens

Multi-agent review/gate pipelines, adversarial concordance, the
verdict-and-FIX loop (the authority model both RULES and APPLIES the fix),
and regression-safe edits across worktrees.

## Principles (from the verdict)

- Pointers, not payloads — diffs, review findings, and fix plans are
  `repo://` artifacts, never inlined into handoffs.
- Filesystem is the contract — review artifacts are `.json` files with a
  known schema, machine-parseable by any agent.
- Start simple + measure — each capability lands as an opt-in field or
  pointer. No macro, no implicit wiring.
- Deterministic where claimed — reviews that *disagree* are the desired
  outcome; the wiring (which reviewer runs when, what they receive) must be
  reproducible from the tasks file alone.
- No cloud lock-in — all artifacts, schemas, resolvers are local.
- Honours DESIGN.md §6 rejects (no macro expansion, no strategy field in
  coordinate layer, no schema bump for handoff v1.2).

---

## Capability 1: Diff materialization — make every worktree diff a first-class artifact

**Pitch:** After a worktree-isolated task, the full diff is written to a
known path so downstream review tasks `receives:` it — the diff is a
pointer, not a payload.

**Builds on:** `_worktree_commit_and_remove` (`coordinate.py:930-947`), the
existing `diffstat` capture; `receives:` (DESIGN §2d, Phase 3);
`config.py` gets one new path property.

**Mechanism:**
1. Add `coordinate_diffs_dir` property to `Config`:
   `self.root / self.data["coordinate"]["runs_dir"] / ".." / "diffs"`
   (parallel to `runs`, `events`, `worktrees`). Default:
   `<contract>/coordinate/diffs/`.
2. In `_worktree_commit_and_remove`, after the commit succeeds, write the
   full diff:
   ```python
   diff_text = _git(wt_dir, "diff", "HEAD~1", "HEAD", check=False).stdout
   if diff_text.strip():
       diff_dir = config.coordinate_diffs_dir / run_id
       diff_dir.mkdir(parents=True, exist_ok=True)
       diff_path = diff_dir / f"{task_id}.diff"
       diff_path.write_text(diff_text)
   ```
3. The downstream task declares `receives: ["repo://.pigeon/coordinate/diffs/<run_id>/<task_id>.diff"]`.
4. The `receives:` resolver (DESIGN §2d step 3) checks existence — if the
   diff file is missing (worktree not yet committed), it drops + warns.

**Why a force-multiplier:**
- A review task no longer needs access to the full worktree branch or the
  agent's entire output — it sees exactly what changed.
- Decouples diff *production* (worktree commit) from diff *consumption*
  (any downstream task, any runner, any topology).
- Diff files are small, deterministic, `repo://`-resolvable, and
  cacheable.

**Hardest problem: LFS-scale diffs.** A single task could produce a
multi-MB diff (generated code, binary lockfile changes). Mitigation:
`_git diff --diff-filter=ACM -- .` (omit deletions from the reviewable
diff — the downstream reviewer cares about what was added/changed); a
per-pool `max_diff_kb` config cap; beyond the cap, emit a `diff-too-large`
artifact that references the commit hash (point the reviewer to
`git show <commit>`).

---

## Capability 2: Verdict re-entry loop — the ruler also applies

**Pitch:** A verdict agent can return `verdict: rework` with structured
fix instructions; the Coordinator bounded-loop re-spawns the original
producer task, injecting the fix plan as an artifact — closing the
"RULES AND APPLIES" loop.

**Builds on:** `receives:` (DESIGN §2d), handoff `state.decisions`,
`state.artifacts`, the scheduler's `needs:` DAG at `coordinate.py:1292-1342`,
`_build_command` for prompt injection.

**Mechanism:**
1. A verdict task's return handoff may include:
   ```json
   "state": {
     "decisions": {
       "verdict": "rework",
       "fix_instructions": "Refactor the auth handler to use dependency injection per finding auth-003",
       "max_reentry": 2
     },
     "artifacts": [
       "repo://.pigeon/reviews/<sid>/concordance.review.json"
     ]
   }
   ```
2. After the scheduler loop, in the post-run pass (`coordinate.py:1376-1432`),
   before `recorder.finish`, the coordinator inspects every return handoff:
   - If `decisions.verdict == "rework"`:
     - Check `reentry_counter[tid]` against `max_reentry` (from decision or
       config default: 2). If exceeded, mark the verdict task as
       `failed` with reason `max-reentry-exceeded`.
     - Compute the *delta* between this rework and the previous round: the
       diff of the diff. If the fix produced zero or *larger* diff than
       the prior round, stop (convergence detection).
     - Build a new task on the fly (not in the original tasks file):
       `id = f"fix-{original_id}-v{reentry_counter}"`,
       `doing = fix_instructions`,
       `needs = []`,
       `isolation = worktree`,
       `receives = verdict.artifacts + [diff pointers from the round]`,
       `runner = producer's original runner`,
       `prompt = "You are a fix agent. Apply the following fix plan strictly: {fix_instructions}. Read the review findings at {review_artifact}."`.
     - Append the fix task to the task list. Re-compute waves.
     - Set `reentry_counter[original_id] += 1`.
     - After the fix task, the **gate chain re-runs**: the fix task's
       handoff includes `state.done: ["fix of " + original_id]`, and
       the next iteration flows through `needs:` again.
3. The re-entry counter is stored per `original_task_id` in a
   `dict[str, int]` on the `RunRecorder` or a standalone `_reentry` dict
   scoped to `run_coordinate`.
4. Convergence detection: before re-spawning, compare the fix task's
   diff size (`git diff HEAD~1 --stat`) to the prior round's diff size.
   If it grew or stayed the same, the loop is thrashing — abort.

**Why a force-multiplier:**
- The review pipeline is no longer a one-shot gate: it iterates until the
  change meets the bar, without human intervention.
- The verdict agent is the *authority* — it both judges the output AND
  directs the fix, eliminating the human-in-the-loop for routine review
  cycles.
- Bounded re-entry + convergence detection prevent infinite loops; each
  round is auditably recorded as a new handoff.

**Hardest problem: Guaranteeing convergence.** A fix agent may produce a
worse or equivalently problematic output. The diff-shrink heuristic needs
a tunable `min_shrink_ratio` (default 0.5 — each fix must reduce the diff
by at least 50% line count). If the diff grows, the loop aborts and the
verdict task records `failed: rework-diverged`. This is a heuristic, not a
proof, but it catches the common thrash case.

---

## Capability 3: Structured review artifacts — `.review.json` schema

**Pitch:** A standardised JSON schema for review findings that every
reviewer produces, concordance merges, and verdict consumes — making
review output a first-class pigeon artifact, not prose in a handoff.

**Builds on:** `handoff.schema.json` (the validation-on-receipt pattern),
`state.artifacts`, `repo://` pointer scheme, `resolve.py`.

**Mechanism:**
1. New schema at `<contract>/review.schema.json` (JSON Schema draft
   2020-12), pinned to `SCHEMA_VERSION`. Key shape:
   ```json
   {
     "schema_version": "1.0",
     "target": "repo://path/to/reviewed-file.ext",
     "findings": [
       {
         "severity": "error" | "warning" | "info",
         "line_start": 42,
         "line_end": 48,
         "rule": "rule-id-from-playbook",
         "message": "Use a constant instead of a magic number",
         "suggestion": "repo://.pigeon/coordinate/diffs/<run>/<task>.diff"
       }
     ],
     "verdict": "approve" | "changes-requested" | "blocking",
     "reviewer": "agent-id"
   }
   ```
2. The handoff for every review/audit/readonly task includes instructions
   (in `crew_instructions()` or the prompt template) to write findings to
   `.pigeon/reviews/<sid>/<task_id>.review.json` and declare the path in
   `state.artifacts`.
3. Concordance tasks collect all `.review.json` files via `receives:`,
   merge findings by `target` + `line_start`, and produce a unified
   artifact with conflict markers where reviewers disagree on the same
   line range.
4. Verdict tasks read the unified artifact; when `verdict: changes-requested`,
   they reference specific findings by `rule` + `line_start` in their
   `fix_instructions` decision.
5. Validation: `validate_review()` mirrors `validate_handoff()`: on
   receipt, verify the artifact against the schema; reject malformed
   reviews early, not at concordance time.

**Why a force-multiplier:**
- Decouples review *production* (any agent, any runner) from review
  *consumption* (concordance, verdict, fix agents).
- Makes reviews auditable per-reviewer: you can trace which agent flagged
  which line, and whether concordance correctly merged them.
- Enables structured diff-to-fix: a finding's `suggestion` pointer lets
  the fix agent apply the exact suggested change without interpreting
  prose. The fix agent writes to `state.decisions.applied_fixes: [rule-id, ...]`.

**Hardest problem: Schema versioning + agent compliance.**
  The reviewing agent must know the schema. The handoff for a review task
  includes the schema path as a `repo://` pointer in `rag` or
  `constraints`, and `crew_instructions()` references it. Without
  agent-level support, the reviewer writes prose findings and the
  concordance task degrades gracefully (no structured merge, just
  concatenation). The schema is *advisory* in v1: agents that follow it
  unlock structured merging; agents that ignore it still produce valid
  review prose. Mandatory validation is reserved for a future v2 where
  the project opts in via `coordinate.review_schema_enforced: true`.

---

## Capability 4: Regression verification gate — test-passing as a first-class verdict

**Pitch:** After an edit task completes in a worktree, a verification task
auto-runs the test suite against the isolated branch and compares results
to main, producing a `regression: none | tests-fail | coverage-gap` verdict
as a structured artifact.

**Builds on:** `isolation: worktree` branch + commit (the `git://` resolver
is deferred, so v1 uses `repo://` pointing to a materialised commit-info
artifact); `receives:` for the producer's diff; `by_agent_report`
(`coordinate.py:706-735`) for per-task metrics aggregation.

**Mechanism:**
1. New config field `coordinate.verify`:
   ```yaml
   coordinate:
     verify:
       test_command: ["pytest", "-x", "--tb=short"]
       deadline_minutes: 10
       auto: true  # auto-generate verify task for every non-readonly worktree task
   ```
2. When `verify.auto: true` (or when a tasks file includes a task with
   `verify: true`), the coordinator auto-generates a verification task
   after every non-readonly worktree task:
   - `id: verify-<producer_id>`
   - `runner: the fastest runner (or a config-specified verify-runner)`
   - `doing: Run the test suite against the proposed changes and report regressions`
   - `needs: [producer_id]`
   - `readonly: true` (auto-worktree: the verifier runs in its own
     worktree containing the test suite + the change)
   - `receives: ["repo://.pigeon/coordinate/diffs/<run_id>/<producer_id>.diff"]`
3. The verify task:
   - Runs `git checkout pigeon/<run_id>/<producer_id>` (or cherry-picks the
     producer's commit into its own worktree)
   - Runs the test command; captures exit code, test output
   - Runs the same test command against `HEAD` (the main branch) for
     comparison
   - Writes a `.review.json`-compatible artifact with:
     ```json
     {
       "schema_version": "1.0",
       "verdict": "approve" | "blocking",
       "findings": [
         {
           "severity": "error" if tests_fail else "info",
           "rule": "regression/tests",
           "message": "3 tests pass, 0 fail, 2 new tests added"
         }
       ],
       "target": "pytest"
     }
     ```
4. The verdict gate reads this artifact: if `blocking`, the re-entry loop
   (Capability 2) triggers; if `approve`, the change is cleared for merge.

**Why a force-multiplier:**
- Test verification is no longer a manual step or a CI-only gate — it runs
  inside the coordination pipeline, before anything merges, consuming the
  same worktree isolation that the producer used.
- The test verdict feeds directly into the review pipeline's DAG (via
  `needs:`) — a failing test blocks downstream merge/release tasks
  automatically.
- The comparison against HEAD gives a **regression signal**, not just a
  pass/fail: a test that passed on main but fails on the branch is a
  regression; a test that was already failing on main is a pre-existing
  condition.

**Hardest problem: Test suite discovery and duration.**
  The coordinator doesn't know which tests exist or how long they take.
  Running the full suite against every edit task is impractical for large
  projects. Mitigations (layered, opt-in):
  - **pytest file mapping** — a project-supplied `coordinate.verify.tests:
    ["tests/**/*.py"]` glob limits scope.
  - **`deadline_minutes`** (DESIGN §2e, already accepted) kills the
    verifier if it takes too long.
  - **`--last-failed` reuse** — if a prior verify task wrote a
    `.pytest_cache` or JUnit XML, the next verify re-runs only the failed
    set plus the files touched by the diff (parsed from `diff --stat`).
  - v1: full-suite + deadline. v2: incremental test selection via diff.

---

## Summary: capability interaction matrix

| Capability | Depends on | Enables |
|---|---|---|
| 1. Diff materialization | DESIGN §2d (`receives:`), worktree commit | Input for 2, 3, 4 |
| 2. Verdict re-entry loop | 1 (diff artifacts), DESIGN §2d, handoff decisions | End-to-end fix pipeline |
| 3. Review artifact schema | 1 (diff pointers), handoff validation | Structured input for 2; audit trail for every lens |
| 4. Regression verification gate | 1 (diff pointers), `isolation: worktree` | Hard evidence for 2's verdict decisions |

Capability 1 is the foundation — a 20-line change to `_worktree_commit_and_remove`
plus one config property. Capabilities 2–4 build on it and on each other,
but each is independently shippable.

All four honour pigeon's ethos: pointers-not-payloads (artifacts are
`repo://` paths, never inline content), filesystem-is-the-contract (review
artifacts are flat files with a validated schema), start-simple (each
capability is an opt-in field), deterministic (the topology is explicit in
the tasks file), and no cloud lock-in (everything is local git + files).

No handoff schema bump. No new pointer scheme beyond `repo://`. No macro
expansion. No strategy/voting fields in the coordinate layer. No in-place
rewrites. Every new path is a `repo://`-resolvable file under the contract
directory.
