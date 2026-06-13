# Concordance Review: Skeptic Assessment of Roadmap Proposals

**Reviewer:** `concord` (agy)  
**Session:** `roadmap`  
**Date:** 2026-06-13  
**Status:** Independent Skeptic Gate Assessment  

---

## 0. High-Level Assessment & Ethos Protection

Pigeon's core philosophy is: **"start simple, measure, and only graduate to heavier machinery where measurement proves it is the bottleneck."** 

Both proposals ([idea-gen-deepseek.md](file:///.pigeon/coordinate/roadmap/idea-gen-deepseek.md) and [idea-gen-mimo.md](file:///.pigeon/coordinate/roadmap/idea-gen-mimo.md)), along with the ideas proposed by `gen-north`, exhibit significant **scope creep and premature complexity**. They attempt to transform Pigeon from a lightweight coordination and context-carrying substrate into:
1. An active runtime test runner and CI engine (Lens B: Regression verification gate).
2. A dynamic scheduling loop that rewrites DAGs on the fly (Lens B: Verdict re-entry loop).
3. A complex local vector/metric database with custom JSON schemas, cache stores, and feedback loops (Lens C: Reasoning Bank, Model Tournament Scoring).

If we adopt these as proposed, we introduce heavy, fragile runtime states, destroy the predictability of plans (breaking `--dry-run`), and leak agent-level reasoning (such as voting, JSON merging, and test diagnostics) into the coordinator.

Below is the skeptic's verdict for each capability: either a **concrete downscope** to keep it simple, or an outright **kill**.

---

## 1. Evaluation of Lens B (Editing & Review — `gen-deepseek`)

### Capability 1: Diff materialization
*   **Pitch:** Write worktree-isolated task diffs to `.pigeon/coordinate/diffs/<run_id>/<task_id>.diff` for downstream consumption.
*   **Skeptic Assessment:** **DOWNSCOPE**. The concept is clean and fits the "pointers-not-payloads" ethos perfectly. However, the proposed "LFS-scale diff mitigation" (with `max_diff_kb` and custom `diff-too-large` fallback artifacts) adds configuration clutter before we have measured any actual large-diff failures. 
*   **Concrete Downscope:** Implement the raw diff writing in `_worktree_commit_and_remove` (~10 lines of python) but **completely drop** the `max_diff_kb` configurations and fallback structures. Rely on the runner CLI or git to handle larger diff files natively in Phase 1.

### Capability 2: Verdict re-entry loop
*   **Pitch:** Dynamically re-run tasks on `verdict: rework` by injecting new tasks into the active scheduler loop and re-computing waves.
*   **Skeptic Assessment:** **KILL**. This is a massive violation of the static DAG model. Fact #3 in `DESIGN.md` enforces that `--dry-run` must print exactly what will run. Dynamically rewriting waves and injecting tasks on the fly makes the execution flow non-deterministic, highly audit-resistant, and prone to infinite loops. Furthermore, implementing convergence heuristics (like `min_shrink_ratio`) inside the coordinator is a category error.
*   **Concrete Downscope/Kill:** Kill the dynamic coordinator-driven loop. If a task fails or needs rework, let the runner script or the verdict task exit with a non-zero code to fail the run (or record the feedback in the handoff). Let the human or an external task runner (like a Makefile or CI script) decide whether to launch a new, clean session.

### Capability 3: Structured review artifacts (`.review.json` schema)
*   **Pitch:** A separate `review.schema.json` format for structured findings that the coordinator validates and merges.
*   **Skeptic Assessment:** **KILL**. This duplicates the existing `handoff.schema.json` schema system. The coordinator should not be in the business of parsing, validating, and merging agent findings (like matching line numbers or resolving conflicting review comments). That leaks agent-level reasoning into the scheduling substrate.
*   **Concrete Downscope/Kill:** Kill the separate schema and coordinate-level merging. Reviewers should output standard markdown or plain JSON artifacts and reference them in the existing `state.artifacts` list of their handoff. Let the concordance *agent* (not the coordinator code) retrieve and merge these documents as a standard task.

### Capability 4: Regression verification gate
*   **Pitch:** Auto-generate a verify task `verify-<producer_id>` that runs `pytest` and compares output against HEAD.
*   **Skeptic Assessment:** **KILL**. Hardcoding test execution details (`pytest`, `.pytest_cache`, and JUnit XML parsing) turns Pigeon into a test runner. Auto-generating tasks dynamically also breaks dry-run contract guarantees.
*   **Concrete Downscope/Kill:** Kill the auto-generated verification tasks. If a project requires test validation, they should define it explicitly as a standard task in the `.tasks.yaml` file (e.g., `needs: [edit-task]`, `runner: pytest`).

---

## 2. Evaluation of Lens C (Local Learning — `gen-mimo`)

### Capability 1: Strategy Extraction from Run Manifests
*   **Pitch:** Extract task outcomes and write JSON strategy records to `.pigeon/memory/strategies/`.
*   **Skeptic Assessment:** **DOWNSCOPE**. This duplicates `distill.py`, which is already responsible for converting runs into committed markdown memory files (`sessions/<sid>.md` and `decisions.md`). Adding a parallel file tree of JSON strategy files alongside markdown summaries is unnecessary overhead.
*   **Concrete Downscope:** Expand the existing `distill.py` module to extract task outcomes and append them as a structured markdown table (e.g. `## Task Outcomes`) within the existing `sessions/<sid>.md` file. The existing retrieval system will naturally index this.

### Capability 2: Gate Verdict Ledger
*   **Pitch:** Capture gate accept/reject decisions and write them as strategy JSON files.
*   **Skeptic Assessment:** **DOWNSCOPE**. Similar to above, this duplicates `distill.py`'s existing decision rendering. 
*   **Concrete Downscope:** Modify `distill.py` to format `state.decisions` from gate tasks into a clean, query-friendly markdown table in the global `decisions.md` file. No new JSON files or verdict folders.

### Capability 3: Strategy-Aware Context Packing
*   **Pitch:** Add a 5th "strategies" layer to `pigeon pack` and parse HTML comments (`<!-- strategies: ... -->`) to track loaded strategies.
*   **Skeptic Assessment:** **KILL**. The existing `pack.py` "memory" layer already retrieves markdown files under `.pigeon/memory/` using BM25. If strategies are stored in `decisions.md` and `sessions/`, the packer will automatically surface them when relevant. A separate layer, custom token-accounting bucket, and brittle comment parsing add zero utility.
*   **Concrete Downscope/Kill:** Kill the 5th pack layer and comment parsing. Let the existing "memory" layer retrieve the distilled tables naturally.

### Capability 4: Model Tournament Scoring
*   **Pitch:** Maintain a model leaderboard (`model_scores.json`) and sort `model_pool` round-robin lists by descending win rates.
*   **Skeptic Assessment:** **KILL**. Dynamic pool sorting makes scheduling non-deterministic, harder to dry-run, and introduces severe feedback loops.
*   **Concrete Downscope/Kill:** Kill active/automatic pool sorting in the coordinator. Keep the round-robin static and predictable. If metrics are needed, aggregate them in `pigeon metrics` as offline diagnostic telemetry for the human to review.

### Capability 5: Strategy Provenance Graph Edges
*   **Pitch:** Index strategy JSON nodes and link them to sessions and models in the graph.
*   **Skeptic Assessment:** **KILL**. Since we have killed the separate JSON strategy files, we do not need dedicated strategy nodes. The existing graph already links files, sessions, and decisions.

---

## 3. Evaluation of Lens D / Platform Vision (`gen-north`)

### Capability 1: Intelligent context bundles (skill projection)
*   **Pitch:** Project playbooks to `.pigeon/playbooks/` per runtime.
*   **Skeptic Assessment:** **KILL**. This duplicates `src/pigeon/skills.py` which already projects playbooks into runtime-native subagent structures.

### Capability 2: Metric-Driven Context Cache
*   **Pitch:** LRU cache for retrieval results under `.pigeon/cache/` to reduce redundant searches.
*   **Skeptic Assessment:** **KILL**. Local lexical and BM25 retrievals are extremely fast (often sub-millisecond). Introducing cache serialization, TTL, and cache invalidation adds high risks of returning stale context to the agent, with no real performance gain.

### Capability 3: Unified Context Supergraph
*   **Pitch:** Build `.pigeon/graph.json` linking files and decisions.
*   **Skeptic Assessment:** **DOWNSCOPE**. This already exists in `graph.py`. Do not create a new "supergraph" module; only apply standard incremental bugfixes to the existing graph module if needed.

### Capability 4: Adaptive Subagent Pool Orchestrator
*   **Pitch:** Dynamically resize subagent pools based on queue depth and metrics.
*   **Skeptic Assessment:** **KILL**. Introduces complex threading and queue management. Simple concurrency caps are more than sufficient.

---

## 4. Critical Risks the Sonnet Gate is Likely to Miss

The main Sonnet gate, which focuses primarily on clean code patterns and straightforward logic, is highly likely to miss the following systemic runtime failure modes:

### 1. Feedback Loop Bias and Exploration Starvation in Pool Sorting
Sorting the round-robin `model_pool` dynamically based on win rates (Mimo's Capability 4) creates a devastating feedback loop:
*   If a new or cheaper model starts with a single failure (e.g., due to a transient API rate limit, network timeout, or ambiguous task spec), its win rate immediately drops to 0%.
*   The coordinator will demote it to the end of the pool queue, meaning it will **never be selected again** for that task type.
*   This starves the model of any opportunity to "prove" its actual performance under corrected conditions or updated API releases, rendering the pool unable to recover from transient failures.

### 2. Semantic Drift and Code Bloat in the Automated Re-entry Loop
Using diff line count / size metrics (`min_shrink_ratio` in DeepSeek's Capability 2) as a proxy for rework convergence is semantically blind:
*   An agent trying to fix a bug might perform a necessary refactoring that *increases* code size (adding validation, comments, or structure), which would incorrectly trigger a loop abort.
*   Conversely, an agent might thrash by repeatedly modifying the same few lines of code back and forth. The diff size remains constant or shrinks, tricking the heuristic while the task remains completely broken.
*   Running an automated re-entry loop without human oversight risks **semantic drift**, where the agent continuously mutates peripheral code to satisfy a narrow test assertion, causing silent regression failures elsewhere.

### 3. Worktree Cache Stale-Context Hazard
Introducing a local retrieval cache (North's Capability 2) creates a high-risk state mismatch. If an agent modifies a file in a worktree and runs a query, a cached response might return code context from `HEAD` on the main branch, delivering stale code references to the agent and causing silent build errors. Local searches are so fast that caching them is a net negative.

---

## 5. Summary Verdicts Matrix

| Proposer | Capability | Verdict | Skeptic Action / Downscope |
| :--- | :--- | :--- | :--- |
| **DeepSeek** | Diff materialization | **DOWNSCOPE** | Write diffs to disk, but drop all custom size/LFS configuration. |
| **DeepSeek** | Verdict re-entry loop | **KILL** | Do not rewrite DAGs at runtime. Keep scheduling static. |
| **DeepSeek** | Structured review schema | **KILL** | Use standard handoffs + markdown. No custom JSON merging. |
| **DeepSeek** | Regression verification | **KILL** | Keep tests as explicit tasks in `.tasks.yaml`. No auto-injection. |
| **Mimo** | Strategy extraction | **DOWNSCOPE** | Distill outcomes directly into markdown tables in `sessions/<sid>.md`. |
| **Mimo** | Gate verdict ledger | **DOWNSCOPE** | Distill decisions into markdown tables in `decisions.md`. |
| **Mimo** | Strategy-aware packing | **KILL** | Let the existing memory layer pull distilled markdown naturally. |
| **Mimo** | Model tournament | **KILL** | Keep round-robin static. Do not dynamically sort pools. |
| **Mimo** | Graph edges | **KILL** | Avoid strategy nodes since strategy files are killed. |
| **North** | Context bundles (skills) | **KILL** | Duplicates the existing `skills.py` projection. |
| **North** | Context Cache | **KILL** | Premature optimization with stale-context hazards. |
| **North** | Supergraph | **DOWNSCOPE** | Use the existing `graph.py` instead of building a new one. |
| **North** | Adaptive orchestrator | **KILL** | Unnecessary queue scaling complexity. |
