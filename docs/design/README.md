# Design records

Forward-looking design for pigeon, produced by **dogfooding pigeon's own
`coordinate` layer** — a multi-model "army" drafts proposals in parallel, two
independent reviewers gate them, and a final authority reconciles the reviews
and writes the verdict. Separate CLIs, no shared context window; the contract is
the filesystem.

These are committed here (out of the gitignored `.pigeon/coordinate/` runtime
tree) so they survive a `git clean` and are reviewable in history.

## Read this first

| File | What it is |
|------|------------|
| [`PLAN.md`](PLAN.md) | **The roadmap.** Four pillars (Army · Edit/Review/Verify · Reasoning Bank · Empirical Model Selection), the local learning loop, a session-sized phased build (A–F), success metrics, and an explicit "not building" list. |
| [`DESIGN.md`](DESIGN.md) | **Pillar 1 in full** — first-class multi-model ("army") support: `model:`/`model_pool:` fields, `sha1(sid)`-seeded round-robin, cross-wave `receives:` pointer injection, clock-only throttling. `PLAN.md` treats this as decided. |

`inputs/` holds the raw brainstorm artifacts each verdict reconciled, kept for
provenance:

- `inputs/army-design/` — the four free-model proposals + the two reviews
  (Sonnet `review-triage.md`, agy `review-concord.md`) that produced `DESIGN.md`.
- `inputs/roadmap/` — the lens proposals (`idea-gen-*.md`) + the two reviews
  (`triage.md`, `concord.md`) that produced `PLAN.md`.

## How they were produced (honest provenance)

Two `pigeon coordinate` runs, topology **army → gate · concordance → verdict**:

1. **`army-design`** → `DESIGN.md`. Four free opencode models drafted; Sonnet +
   agy gated; Opus 4.8 ruled. One model (nemotron) flaked without writing; the
   gate routed around it on the strength of the other three.
2. **`roadmap`** → `PLAN.md`. Four lenses (Creation/Production, Edit/Review,
   Local Learning, Platform Vision) → Sonnet `triage` + agy `concord` → Opus
   `verdict`. Of the four generators, **`deepseek` (Edit/Review)** and **`mimo`
   (Local Learning)** wrote clean proposals; **`nemotron` (Creation/Production)**
   hit an upstream provider timeout and **`north` (Platform Vision)** dumped a
   partial to stdout without writing — so `PLAN.md` was synthesized from those
   two lenses plus `DESIGN.md` and both gate reviews. The missing lenses are
   largely subsumed by Pillar 1 (parallel generation) and Pillar 4 (win-rate
   measurement); a future re-run can fold them in.

A reliability note from run 2: concurrent opencode instances share one
WAL-mode SQLite DB and deadlock on its lock. The army runners in
`.pigeon/config.yaml` give each instance its own `XDG_DATA_HOME` (its own DB,
seeded with auth) and wrap every runner in `timeout`, which is what let the full
pipeline finish in ~16 minutes.

The "Authority: verdict (Claude Opus 4.8)" headers in the documents record which
model rendered each verdict; they describe the artifact's origin, not its status
as a decision — these are proposals to act on, not commitments.
