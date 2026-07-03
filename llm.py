"""LLM calls for the two custom reasoning steps:

  - extract_decision(commit_msg, diff) -> dict | None
      Pulls a durable engineering decision out of a commit. Returns None if there
      isn't one (most commits don't encode a decision).

  - judge_contradiction(diff, recalled_decisions) -> dict
      Decides whether a new diff violates any recalled past decision. Semantic,
      not literal ("uses fetch" must be caught even if the memory says "use
      apiClient" in different wording).

Uses the Anthropic SDK directly, independent of Cognee's configured LLM backend.
"""
from __future__ import annotations

import json
import re
from typing import Any

import anthropic

from config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL

_client: anthropic.Anthropic | None = None


def _client_get() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not ANTHROPIC_API_KEY:
            raise SystemExit("ANTHROPIC_API_KEY not set in .env")
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def _extract_json(text: str) -> dict | None:
    """Pull a JSON object out of a model response (handles ```json fences)."""
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fence.group(1) if fence else text
    # Fall back to first {...} block.
    if not candidate.strip().startswith("{"):
        m = re.search(r"\{.*\}", candidate, re.DOTALL)
        if m:
            candidate = m.group(0)
    try:
        return json.loads(candidate)
    except Exception:
        return None


def _ask(system: str, user: str, *, max_tokens: int = 1200) -> str:
    resp = _client_get().messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    # Concatenate text blocks
    parts = []
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "\n".join(parts)


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
    return {
        "conflict": bool(data.get("conflict", False)),
        "decision_violated": data.get("decision_violated", "").strip(),
        "explanation": data.get("explanation", "").strip(),
        "confidence": float(data.get("confidence", 0.0) or 0.0),
    }