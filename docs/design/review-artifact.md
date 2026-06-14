# The review-artifact convention (Phase D)

A *convention*, not an enforced schema. Pigeon's coordinator deliberately does
**not** parse review output — that would leak agent reasoning into the scheduler
(PLAN.md ruling #3). Instead, reviewers write a file in the shape below and list
it in their handoff's `state.artifacts`; the concordance agent reads those
pointers and merges them. The contract is the filesystem; the shape is honored by
agents (the `code-reviewer` / `review-concordance` playbooks), not validated by code.

This is the editing-and-review half of the loop, built on the earlier phases:
the edit runs isolated and its **full diff is materialized** to the shared tree
(Phase B); review and verify **`receives:`** that diff by glob (Phase C); nobody
copies the diff into a prompt (pointers, not payloads).

## Review artifact (one per reviewer)

Written to `.pigeon/coordinate/reviews/<sid>-<reviewer-task-id>.json`:

```json
{
  "reviewer": "<task-id>",
  "target": "repo://.pigeon/coordinate/diffs/<run_id>/<edit-task>.diff",
  "verdict": "approve | request-changes",
  "findings": [
    {
      "file": "src/pkg/mod.py",
      "line": 42,
      "severity": "blocker | major | minor | nit",
      "title": "one-line summary",
      "detail": "what is wrong and why it matters",
      "suggestion": "the concrete fix — a diff hunk or precise instruction"
    }
  ]
}
```

`verdict` is `request-changes` iff any `blocker`/`major` finding exists. An empty
`findings` with `approve` is valid.

## Verify artifact (the verification task — 2c)

The verify task is an **ordinary task** (`needs: [edit]`, `receives:` the diff)
whose runner runs the suite. No coordinator magic, no auto-generation. It writes
`.pigeon/coordinate/reviews/<sid>-<verify-task-id>.json`:

```json
{ "result": "pass | fail", "summary": "12 passed, 0 failed", "log": "repo://..." }
```

## Concordance artifact (the merge — 2b)

The concordance agent (`review-concordance` playbook) `receives:` every review +
verify artifact and writes `.pigeon/coordinate/reviews/<sid>-concordance.json`:

```json
{
  "verdict": "approve | request-changes",
  "verify": "pass | fail | unknown",
  "accepted": [ { "...finding...", "raised_by": ["reviewerA","reviewerB"] } ],
  "rejected": [ { "title": "...", "raised_by": ["reviewerC"], "reason": "..." } ],
  "rationale": "where reviewers disagreed and how it was ruled"
}
```

The `accepted` list is the precise, deduped fix list a downstream fix task — or a
bounded verdict-and-fix re-entry (Phase F, if it is ever built) — consumes via
`receives:`.

## Topology

See `examples/edit-review-verify.tasks.yaml`:

```
edit (worktree) ──▶ review  ─┐
                └─▶ verify  ─┴─▶ concord
```

`edit` materializes a diff; `review` and `verify` receive it; `concord` receives
both their artifacts and rules. Swap `edit`'s runner for a free army model
(`model_pool:`) to have a cheap model draft and better models review.
