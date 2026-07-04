# CodeMind - Cross-Repo Shared-Memory Proof (real data)

A repo with **no local CodeMind state** still gets a contradiction caught by the
**shared Cognee Cloud graph** another repo populated. The catch comes ENTIRELY
from the graph - `local signals: 0`.

## The catch

**Repo audited:** `topoteretes/cognee` (dataset `codemind_cognee`)
**Proof commit:** `4e00e50b` - "feat: increase LLM retry"

- **conflict:** `True`
- **decision violated:** Use `GenericAPIAdapter.MAX_RETRIES` for the `max_retries`
  parameter instead of hard-coding a value.
- **explanation:** The diff introduces a new shared retry config
  (`retry_config.py`) and replaces hard-coded stop attempts with
  `llm_retry_stop_condition` (module-level constants `LLM_MIN_RETRY_ATTEMPTS=2`,
  `LLM_MIN_RETRY_SECONDS=240`), but it does NOT use `GenericAPIAdapter.MAX_RETRIES`
  as the established decision mandated - silently moving off the class constant.
- **confidence:** 0.86
- **retrieval:** `local signals: 0 | semantic recall: 4 | graph nodes: 12`

**Cited graph node (from the shared graph):**
> Decision: Use GenericAPIAdapter.MAX_RETRIES for the max_retries parameter
> instead of hard-coding a value. Rationale: Centralizes retry configuration,
> making it easier to adjust globally and ensuring consistent retry behavior
> across primary and fallback API calls. Scope: cognee/infrastructure/llm

## Why this is the cross-repo proof

The detection ran with the local `memory_registry.json` **blanked** - simulating
repo B: a repo with no own CodeMind state, no hand-seeded registry. The only
memory in play is the shared Cognee Cloud graph that the audit's ingest of
cognee's older history populated. `local signals: 0` means the catch came
**entirely from the shared graph** - `semantic recall: 4` + `graph nodes: 12`.

That is the Cloud-native differentiator: one memory graph across your whole
org's repos. A hard-won decision ("use MAX_RETRIES") made in one commit
protects a later PR that silently moves off it - and the detecting repo had to
remember nothing locally. No local/self-hosted memory can do this. The
hand-seeded Redis demo proves the mechanism; this proves it on **real cognee
code** with a real contradiction.

## Verification

This is Catch 1 from the fair-split audit ([`CAUGHT.md`](CAUGHT.md)), re-derived
here as the cross-repo hero. The verdict is the real output of `detect_core()`
(the same core that runs in CI) on commit `4e00e50b` against the shared graph.
Ground-truth verification (the commit really does NOT use `MAX_RETRIES`) is in
[`EVAL.md`](EVAL.md).

Reproduce the graph-only detection:
```bash
# ingest cognee's older history into the shared graph
python scripts/audit_repo.py --repo /tmp/cognee_real --dataset codemind_cognee \
    --max-count 80 --scan 40 --out CAUGHT.md
# then re-run detection on the catch with the local registry blanked
python scripts/cross_repo_proof.py --repo /tmp/cognee_real \
    --dataset codemind_cognee --head 4e00e50b
```