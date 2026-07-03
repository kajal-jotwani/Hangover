"""Optional GitHub PR-comment poster. If GH_TOKEN is set, posts a real comment;
otherwise falls back to terminal + file. Keeps the demo reliable without a PAT."""
from __future__ import annotations

import json
import os

import requests

from config import GH_PR_NUMBER, GH_REPO, GH_TOKEN, PENDING_CONFLICT_PATH
from registry import load_registry


def _format_comment(conflict: dict, *, sha: str = "") -> str:
    dec = conflict.get("decision_violated", "")
    conf = conflict.get("confidence", 0.0)
    expl = conflict.get("explanation", "")
    # Try to enrich with the source commit + importance from the registry.
    importance = ""
    src = sha
    # Match the violated decision to a registry entry for richer context.
    from registry import find_by_decision_text
    entry = find_by_decision_text(dec)
    if entry:
        importance = f"\n(memory importance: {entry.get('importance', '?')})"
        src = src or entry.get("sha", "")
    src_line = f" (from commit {src[:8]})" if src else ""
    return (
        "⚠️ **CodeMind: this change may contradict a past decision.**\n\n"
        f"> {dec}{src_line}{importance}\n\n"
        f"{expl}\n\n"
        "Is this intentional?\n"
        "- **[Confirm change]** — the old decision is superseded; memory will be updated (`improve`/`forget`).\n"
        "- **[This is a bug]** — the old decision stands; please fix the code.\n"
    )


def post_or_print(conflict: dict, *, sha: str = "", quiet: bool = False) -> None:
    """Surface a conflict. Real PR comment if GH_TOKEN set, else terminal+file."""
    body = _format_comment(conflict, sha=sha)
    if GH_TOKEN and GH_REPO and GH_PR_NUMBER:
        url = f"https://api.github.com/repos/{GH_REPO}/issues/{GH_PR_NUMBER}/comments"
        r = requests.post(
            url,
            headers={"Authorization": f"token {GH_TOKEN}",
                     "Accept": "application/vnd.github+json"},
            json={"body": body},
            timeout=20,
        )
        if not quiet:
            print(f"[github] posted comment ({r.status_code}) to {GH_REPO}#{GH_PR_NUMBER}")
    else:
        if not quiet:
            print("\n" + body)
    # Always persist so reconcile.py can pick it up.
    PENDING_CONFLICT_PATH.write_text(json.dumps({**conflict, "sha": sha}, indent=2))
    if not quiet:
        print(f"[codemind] conflict written to {PENDING_CONFLICT_PATH}")