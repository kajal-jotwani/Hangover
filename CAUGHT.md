# CodeMind - Real-Code Catch Reel

Real contradictions CodeMind caught in real public repos' own commit history. Each entry is a commit CodeMind flagged as contradicting a decision it extracted from the same repo's history - caught by the same `detect_core()` that runs in CI on every PR. No hand-seeded data.

Retrieval is **graph-only** (the local registry is blanked for each run), so a catch comes from the Cognee graph - the same path a repo with no prior CodeMind state would take.

**Fair temporal split:** the OLDER half of the audited window is ingested to build the decision graph; only the NEWER half is scanned against it. So a scanned commit is never judged against a decision a LATER commit established - no temporal-leakage false positives. This mirrors the real product: a team's accumulated memory vs. a new commit.

Precision labeling is in `EVAL.md`.

## https///github.com/topoteretes/cognee (dataset `codemind_cognee`)

Ingested 40 commits -> 29 decisions remembered into the Cognee graph. Scanned 40 commits -> **3 catch(es)**.

### Catch 1 - `4e00e50b` feat: increase LLM retry
- **decision violated:** Use GenericAPIAdapter.MAX_RETRIES for the max_retries parameter instead of hard‑coding a value
- **explanation:** The diff introduces a new shared retry config and replaces hard‑coded stop attempts, but it does not use GenericAPIAdapter.MAX_RETRIES as mandated, thereby violating the decision to centralize retries via that constant.
- **confidence:** 0.86
- **retrieval:** local signals: 0 | semantic recall: 4 | graph nodes: 12
- **cited graph node:**
  > Decision: Use GenericAPIAdapter.MAX_RETRIES for the max_retries parameter instead of hard‑coding a value Rationale: Centralizes retry configuration, making it easier to adjust globally and ensuring consistent retry behavior across primary and fallback API calls Scope: cognee/infr

### Catch 2 - `9e9badec` docs: update readme
- **decision violated:** Decision: Use canonical environment variable names (e.g., ENV, COGNEE_SERVICE_URL, COGNEE_API_KEY, LOG_LEVEL, BIND_ADDRESS) and retain legacy names only as fallback for backward compatibility
- **explanation:** The README diff introduces the non‑canonical environment variable COGNEE_BASE_URL instead of the required COGNEE_SERVICE_URL, breaking the rule that only canonical names may be used (with legacy names only as fallback).
- **confidence:** 0.86
- **retrieval:** local signals: 0 | semantic recall: 1 | graph nodes: 7
- **cited graph node:**
  > Readme for the examples directory. __node_content_end__

### Catch 3 - `539e29eb` docs(readme): add demo GIF below logo and intro
- **decision violated:** Use relative paths for assets (e.g., demo GIF) per the relative‑paths policy
- **explanation:** The diff adds an absolute GitHub raw URL for the demo GIF instead of the required relative path (assets/cognee-demo.gif), breaking the relative‑paths decision.
- **confidence:** 0.93
- **retrieval:** local signals: 0 | semantic recall: 2 | graph nodes: 12
- **cited graph node:**
  > Decision: Reference examples/demos/simple_cognee_example.py as the canonical example script for running Cognee Rationale: The previous simple_example.py path is outdated; using the new demo script ensures documentation and users run the correct, up‑to‑date example. Scope: CLAUDE.

