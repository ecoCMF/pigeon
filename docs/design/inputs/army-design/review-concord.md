# Concordance Review: First-Class Multi-Model ("Army") Support for Pigeon

**Reviewer:** `concord` (agy)  
**Session:** `army-design`  
**Date:** 2026-06-13  
**Status:** Independent Assessment (Concordance Gate)  

This review provides an independent evaluation of the multi-model ("Army") design proposals submitted by:
1. [proposal-gen-deepseek.md](file:///home/awcd/pigeon/.pigeon/coordinate/brainstorm/proposal-gen-deepseek.md) (DeepSeek)
2. [proposal-gen-mimo.md](file:///home/awcd/pigeon/.pigeon/coordinate/brainstorm/proposal-gen-mimo.md) (Mimo)
3. [proposal-gen-north.md](file:///home/awcd/pigeon/.pigeon/coordinate/brainstorm/proposal-gen-north.md) (North)

---

## High-Level Comparison Matrix

| Dimension | [DeepSeek Proposal](file:///home/awcd/pigeon/.pigeon/coordinate/brainstorm/proposal-gen-deepseek.md) | [Mimo Proposal](file:///home/awcd/pigeon/.pigeon/coordinate/brainstorm/proposal-gen-mimo.md) | [North Proposal](file:///home/awcd/pigeon/.pigeon/coordinate/brainstorm/proposal-gen-north.md) |
| :--- | :--- | :--- | :--- |
| **(a) Model Field / Placeholder** | Optional `model` field in task; `{model}` in templates. Leaves literal if unset. | Optional `model` field; strips `-m ""` if unset using a regex/token post-pass. | `model` field; fails preflight if absent in multi-model runner config. |
| **(b) Model Pools & Round-Robin** | `coordinate.model_pools` config. Explicit `pool:<name>` prefix on tasks. | `model_pools` config. Explicit `model_pool` field on tasks. | `model_pools` config. Weighted distribution and simple round-robin. |
| **(c) Army Topology & Gates** | Task-level `gate: true` with `concordance` resolution strategies (`majority`, etc.). | Session-level `topology:` schema for validation only. | Session-level `species: army` and `army` waves block. |
| **(d) Cross-Wave Pointers** | `receives:` config. Spawns supplementary handoff files via `supplement` pointer. | Two-phase dynamic handoff build (inter-wave); injects pointers into `state.artifacts`. | Lazy resolution of `repo://` pointers. |
| **(e) Telemetry & Free Models** | `budget.free` config. Sliding-window rate-limiter, retry on 429/503. | `RateLimitTracker` (RPM/TPM). `deadline_minutes` wall-clock budget. | `ModelRateLimitTracker` (tokens/hour) and wall-clock rate limits. |

---

## Section-by-Section Keep / Flag / Fix Analysis

### (a) `model:` Task Field + `{model}` Placeholder
*   **Keep:** The task-level `model` field and the `{model}` placeholder expansion in runner templates. Decoupling the CLI runner from the target model is cleaner than N×K runner config entries.
*   **Flag:** [Mimo](file:///home/awcd/pigeon/.pigeon/coordinate/brainstorm/proposal-gen-mimo.md)'s post-substitution cleanup that strips `-m ""` from commands. This token-filtering heuristic is fragile, assuming option flags are always formatted with separate tokens (e.g. it fails if templates use `--model={model}` or if other options rely on empty values).
*   **Flag:** [DeepSeek](file:///home/awcd/pigeon/.pigeon/coordinate/brainstorm/proposal-gen-deepseek.md)'s behavior of leaving `{model}` literal in templates if `model` is unset. This will cause command execution failures at runtime.
*   **Proposed Fix:** 
    1. Update [preflight](file:///home/awcd/pigeon/src/pigeon/coordinate.py#L308) in [coordinate.py](file:///home/awcd/pigeon/src/pigeon/coordinate.py) to validate that if a runner template contains `{model}`, the task (or defaults) *must* supply a resolved model value. Otherwise, reject the plan immediately.
    2. Avoid post-substitution stripping. Keep template substitutions clean and explicit.

### (b) Named Model Pools + Round-Robin
*   **Keep:** The `model_pools` configuration mapping. We should use [Mimo](file:///home/awcd/pigeon/.pigeon/coordinate/brainstorm/proposal-gen-mimo.md)'s schema pattern (explicit `model_pool:` task field) rather than [DeepSeek](file:///home/awcd/pigeon/.pigeon/coordinate/brainstorm/proposal-gen-deepseek.md)'s string-prefix `model: pool:<name>` to simplify JSON schema validation.
*   **Flag:** [North](file:///home/awcd/pigeon/.pigeon/coordinate/brainstorm/proposal-gen-north.md)'s weighted distribution. Simple round-robin is cheaper to maintain and sufficient.
*   **Proposed Fix:** 
    Resolve `model_pool` to a concrete `model` string during [load_tasks](file:///home/awcd/pigeon/src/pigeon/coordinate.py#L149). To prevent parallel sessions from always selecting the same first model in a pool, initialize the round-robin index per session by seeding it with a hash of the `sid` (Session ID).

### (c) Army → Gate(Concordance) → Verdict Topology
*   **Keep:** Reusable DAG wave coordination.
*   **Flag:** [DeepSeek](file:///home/awcd/pigeon/.pigeon/coordinate/brainstorm/proposal-gen-deepseek.md)'s complex concordance resolution strategies (`majority`, `unanimous`, etc.) implemented in the coordinate scheduling layer. This leaks agent-level reasoning and voting logic into the coordination layer.
*   **Flag:** [DeepSeek](file:///home/awcd/pigeon/.pigeon/coordinate/brainstorm/proposal-gen-deepseek.md)'s and [North](file:///home/awcd/pigeon/.pigeon/coordinate/brainstorm/proposal-gen-north.md)'s dynamic/implicit task expansions (e.g. macro expansion of a short `army:` block into 7 tasks). This reduces plan visibility and makes dry-runs harder to audit.
*   **Proposed Fix:**
    Adopt [Mimo](file:///home/awcd/pigeon/.pigeon/coordinate/brainstorm/proposal-gen-mimo.md)'s approach. Keep the tasks file explicit (all 7 tasks declared with their standard `needs:` relations). Use the `topology:` configuration purely as a semantic annotation for validation and formatted printing, rather than a dynamic DAG generation macro. Let downstream verdict tasks (run via agent CLIs) handle consensus or reconciliation using their own crew/verdict steps.

### (d) Cross-Wave POINTERS-NOT-PAYLOADS
*   **Keep:** Dynamic injection of completed upstream outputs into downstream task handoffs.
*   **Flag:** [DeepSeek](file:///home/awcd/pigeon/.pigeon/coordinate/brainstorm/proposal-gen-deepseek.md)'s supplementary handoff files (`supplement: <path>`). Managing multiple handoff JSON files per task adds significant disk-tracking complexity, complicates cleanup, and makes token accounting non-atomic.
*   **Proposed Fix:** 
    Use [Mimo](file:///home/awcd/pigeon/.pigeon/coordinate/brainstorm/proposal-gen-mimo.md)'s two-phase dynamic handoff build. Write handoffs for Wave N+1 only after Wave N tasks have completed, pulling the exact artifact references they generated and embedding them directly into the downstream handoff's `state.artifacts` list.

### (e) Telemetry + Rate-Limit Handling for Free Models
*   **Keep:** Per-model token/cost budget splits, wall-clock run deadlines (`deadline_minutes`), and telemetry reporting via the `model` event field.
*   **Flag:** Dynamic RPM/TPM scheduling ledgers (DeepSeek/Mimo/North). This is fundamentally flawed for a coordinating agent (see details below).
*   **Proposed Fix:** 
    Drop complex rate-limiting ledgers inside the coordinator. Instead, introduce a simple `max_concurrency` cap or a throttle on spawn intervals (e.g. max 1 spawn per 10s) specifically for free model pools.

---

## Critical Gaps the Sonnet Gate is Likely to Miss

The main Sonnet gate, focusing primarily on clean code patterns and straightforward logic, is highly likely to overlook the following complex runtime failures:

### 1. The Git Worktree Isolation Paradox (Broken Cross-Wave Resolution)
Under the current architecture, a task running under `isolation: worktree` writes its output to a temporary workspace, commits the changes to a task-specific branch `pigeon/{run_id}/{task_id}`, and then deletes the worktree folder (see [_worktree_finish](file:///home/awcd/pigeon/src/pigeon/coordinate.py#L897)).
*   **The Problem:** The files created by task A do not exist in the main repository's working directory (`config.root`). When a downstream task B (e.g., triage) is spawned, its worktree is created from `HEAD` (which does not contain A's branch changes).
*   **The Failure:** When the resolver tries to resolve `repo://.pigeon/coordinate/brainstorm/proposal-gen-nemotron.md` (via [resolve](file:///home/awcd/pigeon/src/pigeon/resolve.py#L107)) within task B's context, it looks for the file physically on disk. It will trigger a `FileNotFoundError`, as the file exists only in the git history of the deleted worktree's branch.
*   **The Fix:** 
    Extend [resolve.py](file:///home/awcd/pigeon/src/pigeon/resolve.py) and the handoff pointer schema to support revision-aware references (e.g., `git://<branch_or_commit>:<relpath>`). When a task completes, the coordinator should inject the pointer as `git://pigeon/{run_id}/{task_id}:path/to/artifact`. The resolver will then execute `git show` under the hood (similar to [_resolve_manifest](file:///home/awcd/pigeon/src/pigeon/resolve.py#L62)) to retrieve the content without requiring the file to exist on the local filesystem.

### 2. Reactive Telemetry vs. Active Rate-Limit Defeats
All three proposals suggest that the coordinator should track requests-per-minute (RPM) or tokens-per-minute (TPM) using sliding windows.
*   **The Problem:** The coordinator only receives token and request telemetry *after* a task process exits (via [_run_task](file:///home/awcd/pigeon/src/pigeon/coordinate.py#L1066)'s stdout extraction).
*   **The Failure:** If 4 parallel tasks are spawned on the same free model pool, the coordinator cannot track their active, in-flight token usage or request rates. It will remain blind until they finish. This makes active RPM/TPM throttling ineffective at preventing rate-limit blocks (HTTP 429) during parallel waves.
*   **The Fix:** 
    Delegate individual API call rate-limiting (and 429 backoff retries) to the runner CLI itself (e.g., `opencode` or `agy`). The coordinator should limit its role to coarse-grained throttling: either a concurrency limit on tasks utilizing the same model provider pool, or a minimum delay between task launches.

### 3. Dry-Run Planning Breakage with Dynamic Handoffs
If handoff generation is deferred until runtime waves (as suggested by the inter-wave handoff build in Mimo's proposal), we run into planning problems.
*   **The Problem:** The `plan` command and `coordinate --dry-run` are designed to validate configurations and display the exact CLI execution statements without modifying files.
*   **The Failure:** If downstream handoff files are not created until upstream tasks execute, a dry-run cannot print the actual commands (since the handoff file paths won't exist) or perform token/preflight validation on them.
*   **The Fix:** 
    Implement a "speculative" planning mode. During planning/dry-runs, the coordinator must simulate handoff creation and write mock placeholder references, or output simulated command lines containing anticipated handoff paths, allowing preflight validation to pass without executing actual tasks.
