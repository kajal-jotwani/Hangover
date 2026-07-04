# CodeMind - Real-Code Catch Evaluation

Hand-labeled precision of the catches in [`CAUGHT.md`](CAUGHT.md), verified
against the actual `topoteretes/cognee` git history (the diffs the cites reference).

## Setup

- **Repo audited:** `topoteretes/cognee` (clone at `/tmp/cognee_real`).
- **Window:** the 80 most recent commits.
- **Fair temporal split:** older 40 commits ingested as the decision graph
  (29 durable decisions extracted); newer 40 commits scanned as candidate
  violations. A scanned commit is never judged against a decision a *later*
  commit established, so there are no temporal-leakage false positives.
- **Retrieval:** graph-only (local `memory_registry.json` blanked for the run),
  so every catch comes from the shared Cognee Cloud graph - the same path a repo
  with no prior CodeMind state takes.
- **Judge:** the same `detect_core()` + `judge_contradiction()` that runs in CI
  on every PR. No hand-seeded data, no per-repo tuning.

## Result

**3 catches / 40 scanned. Precision = 2/3 = 0.67.**

| # | commit | subject | verdict | why |
|---|--------|---------|---------|-----|
| 1 | `4e00e50b` | feat: increase LLM retry | **TRUE** | The older decision was "use `GenericAPIAdapter.MAX_RETRIES` for `max_retries` instead of hard-coding." `4e00e50b` introduces `retry_config.py` with module-level constants (`LLM_MIN_RETRY_ATTEMPTS=2`, `LLM_MIN_RETRY_SECONDS=240`) and `llm_retry_stop_condition` - it does NOT use the class constant. A real refactor that moved off the established constant. |
| 2 | `9e9badec` | docs: update readme | **TRUE** | The older env-var-standardization commit (`d8aa1f38`) established canonical names: `COGNEE_SERVICE_URL`, etc., legacy only as fallback. `9e9badec`'s README introduces `COGNEE_BASE_URL` (non-canonical) for cloud mode. A real docs contradiction. (The first cited graph node was generic "Readme for the examples directory" - weak citation, but the violated decision is real and the catch is substantively correct.) |
| 3 | `539e29eb` | docs(readme): add demo GIF below logo and intro | **FALSE** | Judge claimed a "use relative paths for assets" policy was violated by an absolute `raw.githubusercontent.com` GIF URL. The cited graph node was about `simple_cognee_example.py` (unrelated) - the retrieval did not surface a relative-paths decision, so the "decision violated" was rationalized, not retrieved. |

## What this means

On real cognee history, with no hand-seeded data and a fair (no temporal leakage)
methodology, CodeMind caught **2 real contradictions** that no static linter or
snapshot-summarizer would flag - a retry-policy refactor that silently moved off
an established class constant, and a README using a non-canonical env var name
against a just-shipped standardization. Both caught graph-only (`local signals: 0`),
i.e. purely from the shared Cognee graph.

The one false positive was a judge rationalization where the cited evidence didn't
match the claimed decision - a known failure mode of LLM judges and an obvious
target for a citation-consistency guard.

## Reproduce

```bash
python scripts/audit_repo.py --repo /tmp/cognee_real --dataset codemind_cognee \
    --max-count 80 --scan 40 --out CAUGHT.md
python scripts/audit_repo.py --cleanup --dataset codemind_cognee   # restore tenant
```

The cross-repo hero catch (a real token-usage feature revert, caught graph-only
with `local signals: 0`) is in [`PROOF.md`](PROOF.md).