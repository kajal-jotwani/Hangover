# CodeMind — live catch on a real cognee fork PR

The hand-seeded Redis demo proves the mechanism. The fair-split audit
([`CAUGHT.md`](CAUGHT.md) / [`EVAL.md`](EVAL.md)) proves it on real cognee
*history*. This proves it **live, on a real cognee PR, in real CI** — CodeMind's
own GitHub Action catches a contradiction against a decision remembered in the
shared Cognee Cloud graph.

## Setup

- **Fork:** `topoteretes/cognee` → `divysinghvi/cognee`, with CodeMind bolted on
  (python modules + `codemind-pr.yml` live alongside the cognee codebase; the
  cognee code itself is unchanged — see `CODEMIND_FORK.md` in the fork).
- **Shared graph:** the real cognee decision
  *“Use `GenericAPIAdapter.MAX_RETRIES` for the `max_retries` parameter instead
  of hard-coding a value”* (extracted by the audit from cognee's own history,
  see [`PROOF.md`](PROOF.md)) is remembered in the tenant-global Cognee Cloud
  graph (dataset `codemind_cognee`).
- **The PR:** a within-fork PR (`violation` → `main`) that silently replaces
  `max_retries=self.MAX_RETRIES` with a hard-coded `max_retries=5`, dressed up
  as “bump retry budget for flaky upstream.”
  https://github.com/divysinghvi/cognee/pull/1

## What happens

CodeMind CI runs on the PR. The fork has **no `memory_registry.json`** — so
`local signals: 0` and the catch comes **entirely from the shared Cognee Cloud
graph** (`semantic recall` + `graph nodes`). The judge returns:

- **conflict:** `True`
- **decision violated:** Use `GenericAPIAdapter.MAX_RETRIES` for the
  `max_retries` parameter instead of hard-coding a value
- **confidence:** 0.99
- **retrieval:** `local signals: 0 | semantic recall: 1 | graph nodes: 7`

CodeMind posts a **red `CodeMind / memory` commit-status check** on the head SHA
and a **bot comment** on the PR citing the graph node — the same loop that runs
on every PR to the meta-repo, now catching a real contradiction in real cognee
code.

This is the live, real-code version of the cross-repo shared-memory proof: one
tenant-global graph, a decision made in one commit protecting a later PR that
silently moves off it, with the detecting repo remembering nothing locally.