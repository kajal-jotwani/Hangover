"""GitHub PR-comment poster.

Posts real PR comments when GH_TOKEN/GH_REPO/GH_PR_NUMBER are set (e.g. by the
CI workflow on every PR), else falls back to terminal + file so local runs and
the on-camera demo stay reliable without a token.

Idempotency: each conflict comment carries a hidden `<!-- codemind:head=<sha> -->`
marker. Before posting we scan the PR's existing comments for that marker; if one
already matches the current head SHA we skip (so pushing another commit to the
same PR doesn't spam a duplicate). `post_result_comment` (used by reconcile) posts
a plain comment with no marker — every reconcile is an intentional new beat.
"""
from __future__ import annotations

import json
import os

import requests

from config import GH_PR_NUMBER, GH_REPO, GH_TOKEN, PENDING_CONFLICT_PATH
from registry import load_registry

_GH_API = "https://api.github.com"


def _headers() -> dict:
    return {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github+json"}


def _marker(sha: str) -> str:
    return f"<!-- codemind:head={sha[:12]} -->"


def _already_commented(head_sha: str) -> bool:
    """True if a CodeMind conflict comment already exists for this head SHA."""
    if not (GH_TOKEN and GH_REPO and GH_PR_NUMBER):
        return False
    url = f"{_GH_API}/repos/{GH_REPO}/issues/{GH_PR_NUMBER}/comments"
    try:
        r = requests.get(url, headers=_headers(), timeout=20, params={"per_page": 100})
        if r.status_code != 200:
            return False
        marker = _marker(head_sha)
        return any(marker in (c.get("body", "") or "") for c in r.json())
    except requests.RequestException:
        return False


def _format_comment(conflict: dict, *, sha: str = "") -> str:
    dec = conflict.get("decision_violated", "")
    conf = conflict.get("confidence", 0.0)
    expl = conflict.get("explanation", "")
    importance = ""
    src = sha
    from registry import find_by_decision_text
    entry = find_by_decision_text(dec)
    if entry:
        importance = f"\n(memory importance: {entry.get('importance', '?')})"
        src = src or entry.get("sha", "")
    src_line = f" (from commit {src[:8]})" if src else ""
    body = (
        "⚠️ **CodeMind: this change may contradict a past decision.**\n\n"
        f"> {dec}{src_line}{importance}\n\n"
        f"{expl}\n\n"
        "Is this intentional?\n"
        "- **[Confirm change]** — the old decision is superseded; memory will be updated (`improve`/`forget`). Reply `/codemind confirm <reason>`.\n"
        "- **[This is a bug]** — the old decision stands; please fix the code. Reply `/codemind reject`.\n"
    )
    # hidden marker for idempotency (one per head SHA)
    body += f"\n{_marker(sha)}"
    return body


def post_or_print(conflict: dict, *, sha: str = "", quiet: bool = False,
                  post_comment: bool = True) -> None:
    """Surface a conflict.

    - post_comment=True (default): post a real PR comment if GH creds are set, and
      skip if one already exists for this head SHA (idempotent). Then write
      pending_conflict.json so reconcile.py can pick it up.
    - post_comment=False: skip the API comment entirely (used by the reconcile
      workflow's re-derive step so it doesn't double-post) but still write the file.
    - quiet: suppress terminal printing (CI logs use the action output instead).
    """
    body = _format_comment(conflict, sha=sha)
    posted = False
    if post_comment and GH_TOKEN and GH_REPO and GH_PR_NUMBER:
        if _already_commented(sha):
            if not quiet:
                print(f"[github] already commented for head {sha[:12]} — skipping (idempotent)")
        else:
            url = f"{_GH_API}/repos/{GH_REPO}/issues/{GH_PR_NUMBER}/comments"
            r = requests.post(url, headers=_headers(), json={"body": body}, timeout=20)
            posted = r.status_code in (200, 201)
            if not quiet:
                print(f"[github] posted comment ({r.status_code}) to {GH_REPO}#{GH_PR_NUMBER}")
    elif not quiet and not post_comment:
        print("[github] post_comment=False — file only, no PR comment")
    elif not quiet:
        print("\n" + body)
    # Always persist so reconcile.py can pick it up (even when skipping the post).
    PENDING_CONFLICT_PATH.write_text(json.dumps({**conflict, "sha": sha}, indent=2))
    if not quiet:
        print(f"[codemind] conflict written to {PENDING_CONFLICT_PATH}")
    return None  # posted flag not needed by callers


def post_result_comment(body: str, *, quiet: bool = False) -> bool:
    """Post a plain comment (no marker) — used by reconcile to surface the
    after-recall result in CI. Returns True if posted, False otherwise."""
    if GH_TOKEN and GH_REPO and GH_PR_NUMBER:
        url = f"{_GH_API}/repos/{GH_REPO}/issues/{GH_PR_NUMBER}/comments"
        r = requests.post(url, headers=_headers(), json={"body": body}, timeout=20)
        ok = r.status_code in (200, 201)
        if not quiet:
            print(f"[github] posted result comment ({r.status_code}) to {GH_REPO}#{GH_PR_NUMBER}")
        return ok
    if not quiet:
        print("\n" + body)
    return False