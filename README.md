# CodeMind — The Repo That Remembers

**Your repo has opinions. Watch it catch a teammate breaking one — live.**

CodeMind is a persistent memory graph for a codebase that doesn't just store facts
about *why* the code is the way it is — it watches new commits, detects when they
contradict an established decision, and forces a reconciliation moment: confirm the
change is intentional (memory revises itself) or catch a regression before it ships.

> This isn't a linter. It's memory that can be **wrong**, get **challenged**, and
> **correct itself** — the way a real teammate's understanding would.

Built on **Cognee Cloud** for the wemaekdev hackathon.

---

## The problem

Every team loses tribal knowledge constantly. A new hire "cleans up" code that looks
redundant. It wasn't redundant. It ships. It breaks in prod. Static tools (linters,
code-review bots, RAG-over-docs) only see the code *as it is right now* — they have no
memory of *why* it got that way, and no mechanism to notice when someone unknowingly
undoes a hard-won decision. Existing "repo assistant" tools read a repo once and
summarize a **snapshot**. CodeMind's bet: the valuable thing isn't remembering the code,
it's remembering the **decisions** — and noticing when new code violates them.

---

## How the Cognee lifecycle maps to CodeMind

| Cognee primitive | What it does in CodeMind | Where it fires |
|---|---|---|
| `remember()` | Extracts a structured "decision fact" from a commit (what / why / scope / confidence) and stores it in the shared graph | `ingest.py` |
| `recall()` | Retrieves relevant past decisions when a new diff touches related code | `contradiction.py` (hybrid retrieval) |
| `improve()` | Re-weights the graph after a human confirms a contradiction is an **intentional** update — the old belief is revised, not just appended | `reconcile.py confirm` |
| `forget()` | Surgically retires the single superseded memory by `data_id` (not the whole dataset) — the old belief is visibly crossed out | `reconcile.py confirm` |

> **Why Cloud specifically:** if the memory graph lived on one laptop it wouldn't be
> team memory — it'd be personal notes. The graph has to be **shared and consistent
> across every contributor and every CI run** for it to reflect team-wide, ongoing
> consensus. That requires Cognee Cloud, not a self-hosted single instance.

---

## 🧠 Cognee operations callout (for "Best Use of Cognee" judges)

All four lifecycle verbs are used **meaningfully and live**, plus the deeper
`cognee.search` graph-node retrieval, and the GitHub commit-status + issue
integrations:

- **`cognee.serve(url, api_key)`** — routes every op to the shared Cloud tenant (`config.py`); tenant/user headers patched into the session (`cognee_client._inject_headers`).
- **`cognee.remember(text, dataset_name, importance_weight, self_improvement=True)`** — each extracted decision is stored with an importance score; `self_improvement` auto-runs `improve` (`cognee_client.remember_decision`, called from `ingest.py` and `reconcile.py`). The new item's `data_id` is isolated by **diffing the items list before/after** (the list isn't in insertion order).
- **`cognee.recall(query_text, datasets, top_k, auto_route)`** — semantic retrieval over the graph (`cognee_client.recall_decisions`).
- **`cognee.search(query_text, datasets, only_context=True, feedback_influence=…, neighborhood_depth=…, include_references=True)`** — pulls the **actual graph nodes** (Decision + Rationale + keyword tags), deeper than `recall`'s LLM answer. Used in `contradiction.py` to cite the specific node a diff contradicts, and in the dashboard to render the live graph (`cognee_client.search_graph_nodes`). `feedback_influence` is wired so the reconcile `confirm`/`reject` signal can shape future retrieval.
- **`cognee.forget(data_id, dataset)`** — **surgical single-memory deletion** by `data_id` (confirmed working on cloud). This is what makes "old belief crossed out" honest rather than faked (`cognee_client.forget_one` / `forget_many`).
- **`cognee.improve(dataset_name)`** — explicit re-weight after an update (`cognee_client.improve_graph`, best-effort — see note below). Auto-runs via `remember(self_improvement=True)`.

> **Honest caveat:** `cognee.memify()` and `cognee.visualize()` are blocked on
> this cloud tenant — `memify` 404s (`Empty graph projected`) + 422 (`LLM API key
> not set`), and `visualize`/`datasets.list_datasets` crash on a broken local-SQLite
> path in cloud mode. So `improve` stays best-effort and the dashboard renders the
> live graph from `cognee.search(only_context=True)` nodes instead of `visualize()`.
> Every Cognee API the tenant *does* support is leaned on; the ones it blocks are
> documented rather than faked.

### Integrations (the rules: "and integrations")
- **GitHub PR comments** — the conflict posts as a real comment citing the violated decision + Cognee graph evidence (`github.post_or_print`).
- **GitHub commit-status check** — the conflict also lights up the PR check summary as a **failure**; `reconcile confirm` flips it to **success** (`github.post_commit_status`).
- **GitHub issue on reject** — `reconcile reject` auto-opens an issue tracking the caught regression, labeled `codemind` + `regression` (`github.create_issue`).
- **CI on every PR** — two GitHub Actions workflows (`.github/workflows/`) run detection + the comment-triggered reconcile loop (see "CI — runs on every PR" below).

The reconciliation moment — `remember` the update → `forget` the old belief → `improve`
re-weights → re-`recall`/`search` shows the changed answer — is the entire thesis, and it runs live.

---

## Why this is different

Repo Guardian / CodeBase Navigator / Beetle AI / Congming / CodeSage all read a repo
**once** and summarize/analyze it (a snapshot). **CodeMind is the only entry making a
claim about belief over time** — the memory can be wrong, get corrected, and visibly
change its mind. The contradiction moment is the demo, not "codebase assistant."

---

## Architecture

```
demo_repo (git)  ──▶  ingest.py  ──▶  cognee.remember()  ──▶  Cognee Cloud graph
                       (LLM extracts                       (dataset: codemind_repo_memory)
                        decision facts)                              │
                                                                       │ recall()
                                                                       ▼
new diff  ──▶  contradiction.py  ──▶  hybrid retrieval  ──▶  LLM judge  ──▶  conflict
               (semantic recall +          (Ollama Cloud)            │
                path-scope + keyword                                  ▼
                overlap unioned)                              github.post_or_print
                                                                 │
                                                          ┌──────┴───────┐
                                                          ▼              ▼
                                                   confirm (intentional)  reject (bug)
                                                   remember UPDATE         no memory change
                                                   forget old (data_id)    ← caught a real mistake
                                                   improve()
                                                          │
                                                          ▼
                                                   re-call() → answer CHANGED  (the proof)
```

**Local state:** `memory_registry.json` maps each decision to its Cognee `data_id` so
`forget` can target a single memory. `event_log.json` is the append-only "belief changed"
timeline (feeds the dashboard).

### Files
- `config.py` — env, constants, Cognee connection helper
- `cognee_client.py` — async wrapper over the Cognee SDK (captures `data_id` at remember-time)
- `llm.py` — local LLM (Ollama, OpenAI-compatible) calls: `extract_decision()` + `judge_contradiction()` (JSON-structured)
- `git_io.py` — read commits/diffs/branch-diffs via the git CLI
- `registry.py` — local registry + event log + hybrid-retrieval helpers
- `ingest.py` — Phase 1: walk history → extract → remember → registry
- `contradiction.py` — Phase 2: 3-signal retrieval → judge → surface conflict
- `reconcile.py` — Phase 3: `confirm` (remember+forget+improve) / `reject` (no change)
- `github.py` — optional real PR comment if `GH_TOKEN` set, else terminal+file
- `spike.py` — Phase 0 de-risk: confirms surgical `forget` works before building on it
- `scripts/seed_demo_repo.sh` — builds demo_repo with 4 seeded decisions + violation/benign branches
- `scripts/setup.sh` — one-time pre-demo prep (seed + ingest + recall check)
- `scripts/run_demo.sh` — the scripted 2-minute walkthrough
- `dashboard/build.py` — STRETCH: renders the memory graph + belief-changed timeline to HTML; pulls **live Cognee graph nodes** via `cognee.search(only_context=True)` and shows the Cognee lifecycle-API footprint
- `scripts/spike_lifecycle.py` — live spike of the deeper Cognee APIs (`search`, `visualize`, `memify`, `datasets`) against the cloud tenant; documents which work vs. are blocked

---

## Setup

### 1. Environment
```bash
cd codemind
python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env
# Cognee Cloud:  COGNEE_URL (tenant API Base URL), COGNEE_API_KEY (X-Api-Key),
#               COGNEE_TENANT_ID (X-Tenant-Id), COGNEE_USER_ID (X-User-Id)
# Ollama Cloud: OLLAMA_API_KEY (https://ollama.com/settings/keys), OLLAMA_MODEL
# (optional: GH_TOKEN, GH_REPO, GH_PR_NUMBER for real PR comments)
```

### 2. De-risk the thesis-critical verb (Phase 0 spike)
```bash
.venv/bin/python spike.py
# expect: remembered 2 -> forgot 1 -> 1 remains. Confirms surgical forget() works.
```

### 3. Prep the demo (once, before the camera rolls)
```bash
bash scripts/setup.sh
# rebuilds demo_repo, ingests 4 decisions into Cognee, confirms the 'before' recall
# returns the clean answer ("no, must use Redis"). Re-runnable.
```

---

## The demo (2 minutes)

```bash
bash scripts/run_demo.sh   # press ENTER to advance each beat
```

| Time | Beat |
|---|---|
| 0:00–0:20 | The problem: teams lose why-code-is-the-way-it-is. CodeMind gives the repo a memory that argues back. |
| 0:20–0:40 | Show the seeded graph; `recall("can I use an in-memory Map cache instead of Redis?")` → the **old** answer ("no, must use Redis — Mar 2 stale-config incident"). Remember it. |
| 0:40–1:10 | Live commit: a teammate replaces Redis with a per-process in-memory `Map`. `contradiction.py` fires and posts the conflict citing the cache decision. |
| 1:10–1:30 | `reconcile confirm` → `remember` the UPDATE, `forget` the old belief (surgical, by `data_id`), `improve` re-weights. Old belief visibly crossed out. |
| 1:30–1:50 | `recall("can I use an in-memory Map cache instead of Redis?")` again → the answer has **changed** ("yes, superseded as of the update"). The before/after flip is the loop closing. |
| 1:50–2:00 | Close: memory that can be wrong, get challenged, and correct itself. |

### Manual control (for testing)
```bash
.venv/bin/python contradiction.py --repo demo_repo --branch violation   # should flag the Redis decision
.venv/bin/python contradiction.py --repo demo_repo --branch benign      # should stay quiet (same-file refactor)
.venv/bin/python reconcile.py confirm --reason "intentional, rationale updated"
.venv/bin/python reconcile.py reject
.venv/bin/python dashboard/build.py && open dashboard/index.html        # stretch visual
```

---

## Verification (end-to-end)

1. `python spike.py` → remember 2, forget 1, 1 remains. (Phase 0 gate — surgical forget works.)
2. `bash scripts/setup.sh` → rebuilds demo_repo, ingests 4 decisions; the pre-demo recall returns the clean **before** answer ("no, must use Redis"). Re-runnable: `--reset` surgically forgets every registry `data_id` (including any prior UPDATE) so the graph is clean each run.
3. `python contradiction.py --branch violation` → conflict citing the Redis decision; `--branch benign` (a same-file `DEFAULT_TTL` refactor) → no conflict. **Both must hold.**
4. `python reconcile.py confirm` → `event_log.json` gains remember+forget+improve; the contrastive recall now returns the **updated** belief ("yes, superseded"), different from step 2. (Proof the loop closed.)
5. `python reconcile.py reject` → registry unchanged; recall answer unchanged. (Bug-caught branch verified.)
6. `bash scripts/run_demo.sh` → full walkthrough runs clean.
7. **CI (every PR):** push the repo (with committed `memory_registry.json` + `.github/workflows/`) to GitHub, add the Cognee/Ollama secrets, open a PR with the Redis→Map change → `github-actions[bot]` posts the CodeMind conflict comment citing the cache decision; push another commit → **no duplicate** (idempotency); reply `/codemind confirm intentional, single-instance deploy` → after-recall comment shows the answer flipped ("superseded as of the update"); open a second PR with a different change → a fresh comment on that PR's number (proves it works on **all** PRs).

---

## Tech stack
- **Memory:** Cognee Cloud (shared graph across contributors/CI — the whole point)
- **LLM (extraction + judgment):** Ollama Cloud via its OpenAI-compatible endpoint (`https://ollama.com/v1`, Bearer key; e.g. `gpt-oss:120b`). Cognee Cloud runs its own LLM for graph ingestion server-side, so the two are independent.
- **Ingestion:** Python + git CLI reading `git log -p` and branch diffs
- **Integration:** GitHub PR comments via GitHub API (env-gated; terminal fallback by default)
- **Dashboard:** static HTML generated from local state (stretch)

## Cut list (if behind, in order)
dashboard → real GitHub PR comments (fall back to terminal) → second demo scenario → anything beyond the one scripted contradiction. **Never cut the reconciliation loop — it's the thesis.**

---

## CI — runs on every PR (GitHub Actions)

The terminal demo is one thing; the real product runs in CI on **every pull request**,
not a hardcoded PR number. Two workflows live in `.github/workflows/`:

- **`codemind-pr.yml`** — triggers on `pull_request` (opened / synchronize / reopened).
  Checks out the PR head, recalls relevant past decisions from the Cognee Cloud graph,
  judges whether the diff contradicts any of them, and posts a real PR comment when it
  does. Idempotent per head SHA (pushing another commit to the same PR does *not* spam a
  duplicate).
- **`codemind-reconcile.yml`** — triggers on an `issue_comment` starting with
  `/codemind `. Re-derives the conflict from the PR diff (no duplicate comment), runs the
  reconcile loop, and posts the **after-recall** result as a new PR comment — the
  loop-closing beat, automated.

```
PR opened ── codemind-pr.yml ──▶ contradiction.py ──▶ PR comment (⚠️ conflict) ──▶ author/maintainer replies
                                                                                  │
                                /codemind confirm <reason>   OR   /codemind reject
                                                                                  ▼
                       codemind-reconcile.yml ──▶ reconcile.py confirm|reject --ci ──▶ PR comment (✅/⛔ after-recall)
```

### Required repo secrets (Settings → Secrets → Actions)
`COGNEE_URL`, `COGNEE_API_KEY`, `COGNEE_TENANT_ID`, `COGNEE_USER_ID`, `OLLAMA_API_KEY`
(and optional `COGNEE_DATASET`, `OLLAMA_MODEL` to override defaults). `GITHUB_TOKEN` is
auto-provided by Actions and posts comments as `github-actions[bot]`. To post under a
human identity instead, add a `GH_PAT` secret (classic PAT with `repo` scope, or
fine-grained with *Issues: write* + *Pull requests: write*). Both paths use the same
workflow — `GH_TOKEN: ${{ secrets.GH_PAT || secrets.GITHUB_TOKEN }}`.

### Memory convention
Run `bash scripts/setup.sh` (or `ingest.py`) once locally to build the Cloud graph, then
**commit `memory_registry.json`** to the repo so CI has the local decision manifest for
the path-scope + keyword retrieval signals. If the registry is absent, detection
gracefully degrades to semantic-recall-only (no crash). Re-running ingest later refreshes
the graph; the committed registry keeps the local signals in sync.

### Commands (reply to the CodeMind conflict comment)
- `/codemind confirm <reason>` — the change is intentional: old belief crossed out
  (`forget`), `UPDATE` remembered, `improve()` re-weights, after-recall posted.
- `/codemind reject` — the change is a bug: memory unchanged, old belief reaffirmed.