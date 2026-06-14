# Measuring the learning loop (Phase E gate)

The Reasoning Bank (Phase B) only earns its place if recall **pays for itself**.
This is the measurement that decides whether outcome-aware memory stays a default
or is demoted to retrieval-only — run it, don't assume it (PLAN.md §4).

Everything here is read-only and leans on the existing token ledger; nothing in
the coordinator changes based on these numbers (PLAN.md ruling #8).

## 1. Does recall lower cost-per-good-output?

`distill` writes the enriched session record; `pack` injects matching outcome
rows into the next task's bundle. Both are token-accounted:

```bash
pigeon metrics            # overall + by_kind
```

- **Cost ceiling:** `kind: distill` `actual_tokens` must stay within ~5% of the
  pre-enrichment baseline (the dense one-line-per-task format keeps it cheap).
- **Benefit:** `kind: pack` net `saved_tokens` on outcome-aware bundles. Compare a
  window of sessions run *with* the enriched memory against a baseline window
  *without* it.
- **Decision (the gate):** if `kind: pack` `saved_tokens` is net-positive across
  **≥20 sessions**, keep outcome-aware recall as a default. If not, drop the
  recall claim and leave the data retrieval-only — a layer that doesn't pay is not
  shipped.

## 2. Which model should do which work?

```bash
pigeon metrics --by-model              # ranked win-rate / speed / spend
pigeon metrics --by-model --min-runs 5 # raise the sample-size floor
pigeon metrics --by-model --json       # machine-readable, for your own analysis
```

Aggregates every model-tagged task across all run manifests: win-rate (ok vs.
fail), average duration, tokens, cost, and the number of runs. A model below
`--min-runs` shows as *insufficient data* rather than a noisy rank.

This is **diagnostic only**. You (or an agent) read it and edit a `model_pool` in
the tasks file by hand. The coordinator never consumes it to re-sort round-robin —
auto-demotion would starve a model on one transient 429.

## 3. What can this machine even field?

```bash
pigeon agents          # installed agent CLIs, versions, runner-readiness
pigeon agents --json
```

Shows which coding-agent CLIs are on `$PATH`, which already have a configured
runner, and which free-model CLI (opencode) to point heavy generation at — the
supply side of the army the per-model report measures.
