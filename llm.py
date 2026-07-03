"""LLM calls for the two custom reasoning steps, served by a LOCAL Ollama model
via its OpenAI-compatible endpoint (http://localhost:11434/v1).

  - extract_decision(commit_msg, diff) -> dict | None
      Pulls a durable engineering decision out of a commit. Returns None if there
      isn't one (most commits don't encode a decision).

  - judge_contradiction(diff, recalled_decisions) -> dict
      Decides whether a new diff violates any recalled past decision. Semantic,
      not literal ("uses fetch" must be caught even if the memory says "use
      apiClient" in different wording).

Note: Cognee Cloud runs its OWN LLM for graph ingestion server-side; these calls
are purely our extraction/judgment prompts and never touch Cognee's backend LLM.
"""
from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI

from config import OLLAMA_BASE_URL, OLLAMA_MODEL

_client: OpenAI | None = None


def _client_get() -> OpenAI:
    global _client
    if _client is None:
        # api_key is required by the SDK signature but Ollama ignores it.
        _client = OpenAI(base_url=OLLAMA_BASE_URL, api_key="ollama")
    return _client


def _extract_json(text: str) -> dict | None:
    """Pull a JSON object out of a model response (handles ```json fences)."""
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fence.group(1) if fence else text
    if not candidate.strip().startswith("{"):
        m = re.search(r"\{.*\}", candidate, re.DOTALL)
        if m:
            candidate = m.group(0)
    try:
        return json.loads(candidate)
    except Exception:
        return None


def _ask(system: str, user: str, *, max_tokens: int = 1200) -> str:
    """Call the local Ollama model. Uses JSON mode when the model supports it;
    falls back to plain text if not, and the _extract_json parser handles both."""
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    try:
        resp = _client_get().chat.completions.create(
            model=OLLAMA_MODEL,
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.2,
            response_format={"type": "json_object"},
        )
    except Exception:
        # Model doesn't advertise JSON mode -> retry without it; parser still recovers JSON.
        resp = _client_get().chat.completions.create(
            model=OLLAMA_MODEL,
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.2,
        )
    return resp.choices[0].message.content or ""


# ---------------------------------------------------------------------------
# extract_decision
# ---------------------------------------------------------------------------

EXTRACT_SYSTEM = """You are a senior engineer reading a git commit. Your job: detect any DURABLE ENGINEERING DECISION being made — a rule, convention, or rationale that the team intends to hold going forward (e.g. "always use apiClient for HTTP", "cache layer is Redis", "don't simplify this regex because it handles a legacy customer format").

Most commits do NOT encode a decision (refactors, typo fixes, feature work without a rule). If there isn't a clear durable decision, return {"decision": "NONE"}.

Respond with ONLY a JSON object, no prose, matching:
{
  "decision": "<the rule as a short imperative statement, or NONE>",
  "rationale": "<why this rule exists / what happens if broken; empty if NONE>",
  "scope": "<files, paths, or patterns this applies to, comma-separated; empty if NONE>",
  "confidence": <0.0-1.0, how clearly this is a durable decision>
}"""


def extract_decision(commit_msg: str, diff: str) -> dict[str, Any] | None:
    user = f"COMMIT MESSAGE:\n{commit_msg}\n\nDIFF:\n{diff[:8000]}"
    raw = _ask(EXTRACT_SYSTEM, user)
    data = _extract_json(raw)
    if not data:
        return None
    if str(data.get("decision", "")).strip().upper() == "NONE":
        return None
    return {
        "decision": data.get("decision", "").strip(),
        "rationale": data.get("rationale", "").strip(),
        "scope": data.get("scope", "").strip(),
        "confidence": float(data.get("confidence", 0.7) or 0.7),
    }


# ---------------------------------------------------------------------------
# judge_contradiction
# ---------------------------------------------------------------------------

JUDGE_SYSTEM = """You are a code-review agent with access to a team's past engineering decisions (their "memory"). You are given a new code diff and a set of past decisions that may be relevant.

Decide whether the diff VIOLATES, CONTRADICTS, or UNDOS any of those decisions. This must be SEMANTIC, not literal — e.g. a decision saying "always use apiClient for HTTP" is violated by a diff that introduces a direct fetch() call, even though the wording differs. A diff that merely touches the same file without breaking the rule is NOT a conflict.

Respond with ONLY a JSON object, no prose:
{
  "conflict": <true|false>,
  "decision_violated": "<the exact decision text that is violated, or empty>",
  "explanation": "<one or two sentences: what the diff does vs. what the decision requires>",
  "confidence": <0.0-1.0>
}"""


def judge_contradiction(diff: str, recalled_decisions: list[str]) -> dict[str, Any]:
    if not recalled_decisions:
        return {"conflict": False, "decision_violated": "", "explanation": "No relevant past decisions to compare.", "confidence": 1.0}
    decisions_block = "\n".join(f"- {d}" for d in recalled_decisions)
    user = f"PAST DECISIONS (memory):\n{decisions_block}\n\nNEW DIFF:\n{diff[:8000]}"
    raw = _ask(JUDGE_SYSTEM, user)
    data = _extract_json(raw)
    if not data:
        return {"conflict": False, "decision_violated": "", "explanation": f"<judge returned unparseable output: {raw[:200]}>", "confidence": 0.0}
    # Ollama may emit true/false as strings; normalize.
    conflict_val = data.get("conflict", False)
    if isinstance(conflict_val, str):
        conflict_val = conflict_val.strip().lower() in ("true", "yes", "1")
    return {
        "conflict": bool(conflict_val),
        "decision_violated": data.get("decision_violated", "").strip(),
        "explanation": data.get("explanation", "").strip(),
        "confidence": float(data.get("confidence", 0.0) or 0.0),
    }