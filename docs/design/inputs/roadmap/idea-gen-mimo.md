# Lens C — Local Learning: The Reasoning Bank

**Proposer:** gen-mimo | **Session:** roadmap | **Lens:** Local learning loop that compounds.

---

## Grounding

Pigeon's `distill.py` currently captures **what happened**: outcomes (ok/failed/skipped), task results, hand-backs, artifacts touched, measured spend. It regenerates `sessions/<sid>.md` and `decisions.md` from handoffs and run manifests — deterministically, no LLM in the loop.

What it does **not** capture: what **worked**. When a gate rejects a proposal, which flag pattern caused rejection? When a model wins a tournament, which model produced the accepted output? When a constraint prevents a bug, what was the constraint? Today the answer lives only in the agent's transient context window — lost the moment the session ends.

This proposal closes that loop. A file-based reasoning bank captures strategies (what worked), makes them retrievable (`pigeon retrieve`), packs them into future context (`pigeon pack`), and proves value through token accounting.

---

## Capability 1: Strategy Extraction from Run Manifests

**Pitch:** Automatically mine run manifests for success/failure signals and emit structured strategy records — no LLM needed.

**Seam:** `coordinate.py` run manifests (`coordinate/runs/<sid>-<n>.json`) already record per-task `status`, `exit_code`, `duration_s`, `model` (after army lands), and `skipped_because`. `distill.py:_distill_one` already reads these manifests but discards per-task detail after rendering the session summary.

**Mechanism:**

1. New module `src/pigeon/strategies.py` — pure functions, no side effects.
2. `extract_strategies(config, sid)` walks the latest run manifest for a session and emits one strategy record per task that carries a signal:

```json
{
  "id": "strat-<sid>-<tid>-<n>",
  "kind": "task_outcome",
  "task_id": "gen-mimo",
  "model": "opencode/mimo-v2.5-free",
  "runner": "opencode",
  "outcome": "accepted",
  "duration_s": 42,
  "exit_code": 0,
  "tags": ["code-generation", "python"],
  "source_session": "roadmap",
  "source_manifest": "repo://.pigeon/coordinate/runs/roadmap-1.json",
  "ts": "2026-06-13T21:00:00+00:00"
}
```

3. Strategy records land in `.pigeon/memory/strategies/<sid>-<tid>.json` — one file per task, append-only (new sessions add files, never rewrite). The filename encodes session + task for deterministic lookup.
4. Tags are extracted from the task's `doing` field via a simple tokenizer (same `_WORD_RE` logic as `retrieval.py:64-67`). No LLM classification.
5. `_distill_one` gains a call to `extract_strategies` after rendering the session record. Token-accounted as kind `strategies` with actual = strategy files, baseline = the run manifest sections they replace in an agent's reasoning.

**Force-multiplier:** Every future session inherits the success/failure pattern of every past session. A model that consistently exits non-zero on a task type gets demoted automatically. A runner that completes fast gets preferred.

**Hardest problem:** Distinguishing "task failed because the model was bad" from "task failed because the requirements were ambiguous." The initial design uses exit code + duration as coarse signals; richer signals (the gate's reject reasons) arrive in Capability 2.

---

## Capability 2: Gate Verdict Ledger

**Pitch:** Capture gate/verdict accept/reject rulings — the exact decisions that shaped which proposals survived — as retrievable strategy records.

**Seam:** The handoff schema already carries `state.decisions` (arbitrary key-value pairs). Gate tasks (triage, concord, verdict) emit decisions like `{"proposal-gen-mimo": "ACCEPT", "flag-DS-1": "REJECT"}`. `distill.py:_render_decisions` flattens these into `decisions.md` but loses the **outcome of the gate itself** (did the gate pass? which proposals were accepted?).

**Mechanism:**

1. `strategies.py:extract_gate_verdicts(config, sid)` scans handoffs for tasks where `from` matches a known gate role pattern (any handoff where `state.decisions` contains accept/reject keys).
2. For each gate handoff, emit a strategy record:

```json
{
  "id": "verdict-<sid>-<gate_task>-<n>",
  "kind": "gate_verdict",
  "gate_task": "triage",
  "accepted": ["gen-mimo", "gen-north"],
  "rejected": ["gen-deepseek"],
  "reject_reasons": {"gen-deepseek": "DS-1: literal {model} failure"},
  "verdict_model": "anthropic/claude-sonnet-4-6",
  "source_session": "army-design",
  "ts": "2026-06-13T22:00:00+00:00"
}
```

3. These records land in `.pigeon/memory/strategies/verdicts/<sid>-<gate>.json`.
4. The reject reasons are extracted from `state.decisions` keys that contain reject/flag patterns — simple regex matching, no LLM.

**Force-multiplier:** When a new session proposes similar ideas, `pigeon retrieve "gate verdict reject"` surfaces past rejections with their reasons. The agent can self-correct before proposing something already rejected. This is the **compounding loop**: past gates teach future proposers.

**Hardest problem:** Gate decisions are context-dependent — a flag rejected in one session might be valid in another. The strategy record preserves provenance (which session, which gate) so the retriever can surface context, not just a boolean. The agent decides relevance; the bank provides evidence.

---

## Capability 3: Strategy-Aware Context Packing

**Pitch:** `pigeon pack` gains a new memory layer — strategies — that injects relevant past outcomes into the agent's context before work begins.

**Seam:** `pack.py:_LAYERS` defines budget shares: memory (20%), manifest (10%), code (50%), history (20%). The memory layer queries `retrieval.query(task, scope="memory")` which indexes `.pigeon/memory/**/*.md`. Strategy JSON files are currently outside this index (they're `.json`, not `.md`).

**Mechanism:**

1. Strategy JSON files are rendered as compact Markdown index pages by `strategies.py:build_strategy_index(config)`:
   - `.pigeon/memory/strategies/index.md` — a Markdown table of all strategy records, one row per strategy, with tags as `[[wiki-links]]` for graph connectivity.
   - Each strategy's JSON file is also accompanied by a one-line `.md` summary (e.g., `strategies/gen-mimo.md`): `**gen-mimo** on `opencode/mimo-v2.5-free`: accepted, 42s, tags: code-generation python`.
2. `pack.py` gains a 5th layer in `_LAYERS`:
   ```python
   _LAYERS = (
       ("memory", 0.15),      # shrunk from 0.20
       ("strategies", 0.10),  # NEW — past outcomes for this task type
       ("manifest", 0.10),
       ("code", 0.50),
       ("history", 0.15),     # shrunk from 0.20
   )
   ```
3. The strategies layer queries `retrieval.query(task, scope="memory")` with the task text — the BM25 ranking surfaces strategy index pages whose tags match the task. Top-k strategies (accepted + rejected patterns) are injected into the bundle.
4. The strategy layer is token-accounted separately: `kind: pack_strategy`, showing tokens spent on strategy context vs. tokens saved by avoiding repeated mistakes.

**Force-multiplier:** An agent starting a new session immediately sees: "On similar tasks in the past, model X succeeded and model Y failed. Gate Z rejected pattern P for reason R." This is **zero-cost institutional memory** — the agent doesn't need to ask, and the coordinator doesn't need to remember.

**Hardest problem:** Budget competition. Strategies eat into the 4000-token pack budget. The 10% share (~400 tokens) fits ~3-5 strategy summaries. The BM25 ranking must be sharp enough to surface the *right* strategies, not just any. Mitigation: the strategy index page is dense (one line per record), so 400 tokens covers ~20 strategies in the index; only the top-k full records are expanded.

---

## Capability 4: Model Tournament Scoring

**Pitch:** Track which model/runner pairs win and lose across sessions, producing a ranked leaderboard that `coordinate.py` can consult when assigning `model_pool` round-robin order.

**Seam:** `coordinate.py` model pools use a fixed round-robin seeded by `sha1(sid)`. After army lands (DESIGN.md §2b), tasks carry `model:` or `model_pool:`. The run manifest records `model` per task ( Capability 1's extraction). The scoring module reads these extracted strategies.

**Mechanism:**

1. `strategies.py:score_models(config)` aggregates all `task_outcome` strategy records:
   - For each `(model, task_type)` pair: compute `win_rate = accepted / (accepted + rejected)`, `avg_duration`, `total_runs`.
   - Task type is derived from tags (e.g., `code-generation`, `review`, `documentation`).
2. Output: `.pigeon/memory/strategies/model_scores.json`:
   ```json
   {
     "generated_at": "2026-06-13T23:00:00+00:00",
     "scores": {
       "opencode/mimo-v2.5-free": {
         "code-generation": {"win_rate": 0.85, "avg_duration_s": 38, "n": 20},
         "review": {"win_rate": 0.60, "avg_duration_s": 55, "n": 10}
       },
       "opencode/deepseek-v4-flash-free": {
         "code-generation": {"win_rate": 0.70, "avg_duration_s": 42, "n": 15}
       }
     }
   }
   ```
3. `coordinate.py` `load_tasks` gains an optional hook: when `model_pool` is set and `model_scores.json` exists, sort the pool by descending `win_rate` for the task's detected type before applying the `sha1(sid)` offset. This is **advisory** — if no scores exist, plain round-robin is used (backward compatible).
4. Token-accounted: `kind: model_score`, showing the cost of scoring vs. the token savings from model-preferred packs.

**Force-multiplier:** The pool self-optimizes. Models that consistently lose get rotated to later positions (still tried, but not first). This is **empirical model selection** without any LLM judgment — pure exit-code arithmetic.

**Hardest problem:** Cold start. With no history, all models are equal. The first N sessions are pure exploration. The scoring window should be configurable (default: last 50 sessions) to prevent ancient results from dominating. Also, task-type classification via tag tokenizer is coarse — a future refinement could use the handoff's `doing` field as a richer signal.

---

## Capability 5: Strategy Provenance Graph Edges

**Pitch:** Wire strategy records into the entity graph so `pigeon graph` can answer "what strategies influenced this session?" and "which models are connected to which outcomes?"

**Seam:** `graph.py:build_graph` walks handoffs and memory pages, emitting nodes (session, decision, artifact, agent, page) and edges (decided, references, involves, links). Strategy records are currently invisible to the graph.

**Mechanism:**

1. `graph.py` gains a new node type: `strategy`. Each strategy JSON file becomes a node:
   ```
   node("strategy:strat-roadmap-gen-mimo-0", "strategy", "gen-mimo accepted on mimo-v2.5-free")
   ```
2. New edge types:
   - `session —produced→ strategy` (the session that generated the strategy)
   - `strategy —applied_to→ session` (a later session that loaded the strategy via pack — detected by checking if the strategy's ID appears in the pack bundle's sources)
   - `model —associated→ strategy` (the model that produced the outcome)
3. `build_graph` gains a strategy scan pass after the memory vault pass, walking `.pigeon/memory/strategies/**/*.json`.
4. `pigeon graph "mimo-v2.5-free" --hops 2` now surfaces: model → strategies → sessions that used them → their outcomes. This is the **auditable learning loop**.

**Force-multiplier:** The graph becomes a **learning provenance trail**. You can trace from any session back to the strategies that influenced it, and forward to the strategies it produced. This is how you prove the reasoning bank is compounding — not by claiming it, but by querying the graph.

**Hardest problem:** The `applied_to` edge requires detecting that a strategy was loaded into a pack. The simplest mechanism: when `pack.py` injects strategy records, it writes their IDs into the pack bundle's metadata comment (`<!-- strategies: strat-x, strat-y -->`). On the next distill, `build_graph` reads this metadata from the pack's source file. This is brittle (relies on comment parsing) but deterministic and filesystem-native.

---

## Where They Land (File Layout)

```
.pigeon/memory/
├── strategies/
│   ├── index.md                    # Dense Markdown index of all strategies
│   ├── <sid>-<tid>.json            # Per-task outcome strategies
│   ├── verdicts/
│   │   └── <sid>-<gate>.json       # Gate verdict strategies
│   └── model_scores.json           # Aggregated model leaderboard
├── sessions/<sid>.md               # Existing — unchanged
├── decisions.md                    # Existing — unchanged
└── graph.json                      # Existing — gains strategy nodes/edges
```

## How They Are Recalled

| Path | Recall mechanism |
|------|-----------------|
| `pigeon retrieve "model mimo accepted"` | BM25 hits strategy index.md + individual strategy .json files (already under `memory/` scope) |
| `pigeon pack "implement feature X"` | Strategies layer queries BM25 over memory scope; top-k strategy summaries injected into bundle |
| `pigeon graph "mimo-v2.5-free" --hops 2` | Graph traversal: model → strategies → sessions → outcomes |
| `pigeon distill` | After existing distill, runs `extract_strategies` + `extract_gate_verdicts` + `score_models` + `build_strategy_index` |

## How We Prove It Helped

Every operation is token-accounted. The proof is in `metrics.jsonl`:

1. **Pack strategy cost:** `kind: pack_strategy` — tokens spent loading strategy context.
2. **Pack strategy savings:** Compare `actual_tokens` of strategy-aware packs vs. packs without strategies (baseline = pack without the strategies layer). If strategies save more tokens than they cost (by preventing repeated mistakes, reducing trial-and-error), the savings are measurable.
3. **Model score correlation:** Track whether model-score-informed pool ordering reduces `avg_duration_s` and increases `win_rate` over time. Plot `saved_tokens` from `kind: distill` — if strategy extraction adds minimal overhead (target: <5% increase in distill tokens) while the strategy-aware packs show increasing savings over sessions, the loop is compounding.
4. **Graph density:** `pigeon graph stats` — strategy nodes and edges are counted. Growing density over sessions = the learning trail is building.

---

## Implementation Order

| Phase | Scope | Modules touched | Depends on |
|-------|-------|-----------------|------------|
| 1 | Strategy extraction from run manifests | `strategies.py` (new), `distill.py` | — |
| 2 | Gate verdict extraction | `strategies.py`, `distill.py` | Phase 1 |
| 3 | Strategy index + retrieval integration | `strategies.py`, `retrieval.py` | Phase 1 |
| 4 | Strategy-aware packing | `pack.py`, `strategies.py` | Phase 3 |
| 5 | Model tournament scoring | `strategies.py`, `coordinate.py` | Phase 1 |
| 6 | Graph integration | `graph.py`, `strategies.py` | Phase 1 |

Phases 1-3 are the minimum viable loop. Phases 4-6 compound the value. All phases are backward-compatible: absent strategy files, every module behaves exactly as today.

---

## Backward Compatibility

| Addition | Absent ⇒ |
|----------|----------|
| `strategies/` directory | No strategy extraction; distill unchanged |
| `model_scores.json` | Pool round-robin uses plain `sha1(sid)` offset (existing behavior) |
| `strategies` pack layer | Pack uses 4 layers exactly as today |
| Strategy graph nodes/edges | Graph has no strategy nodes; existing nodes/edges unchanged |
| `kind: strategies` metrics | No new metrics events; existing accounting unchanged |

Every new behavior is reached only by adding new files. No existing file is modified in a way that changes its output when strategies are absent.

---

## Ethos Compliance

- **Pointers-not-payloads:** Strategy records carry `source_manifest` and `source_session` pointers, not inline handoff content.
- **Filesystem-is-the-contract:** Strategies are JSON files under `.pigeon/memory/strategies/`. No database, no cloud, no API.
- **Start simple + measure:** Extraction is exit-code arithmetic. Token accounting proves cost/savings. No LLM in the loop.
- **Deterministic where claimed:** Strategy extraction from a fixed manifest is deterministic. Model scores from a fixed set of strategies are deterministic.
- **No cloud lock-in:** Everything is local files, BM25 retrieval, JSON.
