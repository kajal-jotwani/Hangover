# CodeMind as an architecture sandbox

The pitch in one line: **encode your system's architectural invariants as
remembered decisions; CodeMind watches every PR for violations.**

A linter catches syntax/style. A snapshot-summarizer tells you what a PR *did*.
Neither of those knows your architecture — the layering rules, the abstraction
boundaries, the "we decided last quarter that all X goes through Y" tribal
knowledge that lives in a senior engineer's head and nowhere else. CodeMind is
where that knowledge lives, and it fires on the PR that quietly breaks it.

## How it works

1. **Decisions get remembered.** Every merge (and any explicit
   `/codemind remember …`) ingests a durable decision into the shared Cognee
   Cloud graph — the *what*, the *rationale*, and the *scope* (which parts of
   the tree it applies to).
2. **Every PR is judged against the graph.** CodeMind's CI retrieves the
   decisions relevant to the PR's touched files (path-scope + semantic recall +
   graph nodes) and asks the LLM judge: does this diff contradict any of them?
3. **Violations surface as a red check + a bot comment** citing the exact
   decision and its rationale, with Confirm/Reject reconcile buttons so the
   team can either accept the override (the old decision is superseded in the
   graph) or reject the change (the memory holds).

That loop is the sandbox: the graph is your architecture's living rule set, and
CodeMind is the gatekeeper that enforces it on every change.

## A concrete example (live, on real cognee code)

We forked `topoteretes/cognee` to `divysinghvi/cognee` and bolted CodeMind's CI
on. The shared graph remembers an architectural invariant for cognee:

> **All LLM access must go through `get_llm_client()` (the structured-output
> framework). Never instantiate a provider client (`openai.OpenAI`,
> `anthropic.Anthropic`, `litellm`) directly in pipeline, module, or application
> code.**
> *Rationale: `get_llm_client()` centralizes model config, API-key resolution,
> retry/backoff, structured-output (instructor) wrapping, tracing, and fallback.
> Bypassing it silently drops retry handling, observability, and config
> consistency, and breaks the abstraction boundary between application code and
> the LLM infrastructure layer.*

Then we opened a PR that takes a "shortcut":

```python
# cognee/modules/pipelines/tasks/quick_summarize.py
import openai

async def quick_summarize(text: str) -> str:
    client = openai.OpenAI(api_key=__import__("os").environ["OPENAI_API_KEY"])
    resp = client.chat.completions.create(model="gpt-4o-mini", messages=[...])
    return resp.choices[0].message.content
```

…a perfectly plausible-looking change: a new task, a reasonable commit message
("skip the instructor wrapper overhead on this hot path"), and a direct provider
call that punches straight through the `get_llm_client()` abstraction boundary.

**CodeMind caught it live in CI:**

- `CodeMind / memory` check → **failure**
- bot comment citing the invariant above
- retrieval: `local signals: 0 | semantic recall: 1 | graph nodes: 8`
  (the fork has no `memory_registry.json` — the catch comes **entirely from the
  shared Cognee Cloud graph**)
- confidence: 0.98

Live PR: https://github.com/divysinghvi/cognee/pull/2

No static linter flags `openai.OpenAI(...)` — it's valid Python calling a valid
library. The only thing that makes it a *violation* is the architectural
decision that says "don't do that here." That's the knowledge CodeMind carries
that nothing else does.

## Why this is the sell

- **It's architectural, not cosmetic.** The catch is about abstraction
  boundaries and layering — the kind of regression that accumulates into
  "nobody knows why this code is structured the way it is" over a year.
- **The rationale travels with the catch.** The bot comment doesn't just say
  "bad" — it quotes *why* the decision exists, so the PR author learns the
  architecture, not just the rule.
- **It's org-wide, not per-repo.** The decision lives in the shared tenant
  graph; a PR in any repo on the same tenant gets checked against it
  (`local signals: 0`). One memory, many repos.
- **It's reconcilable.** Architecture changes — sometimes the right move *is*
  to bypass the abstraction. The Confirm flow supersedes the old decision in the
  graph so the catch doesn't fire again; the sandbox evolves with the codebase.

## The other examples

- **Low-level constant drift** — a PR that hard-codes `max_retries=5` instead of
  `GenericAPIAdapter.MAX_RETRIES` (https://github.com/divysinghvi/cognee/pull/1).
  Same mechanism, finer-grained invariant.
- **Real history audit** — [`CAUGHT.md`](CAUGHT.md) / [`EVAL.md`](EVAL.md): run
  CodeMind against cognee's own commit history, fair temporal split, precision
  2/3 = 0.67.
- **Cross-repo hero** — [`PROOF.md`](PROOF.md): a catch with the local registry
  blanked, purely from the shared graph.

The architecture-sandbox framing is the headline; these are the evidence.