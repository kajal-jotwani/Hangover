"""Phase 2 — contradiction detector.

Takes a diff (a branch or commit), recalls relevant past decisions via THREE
unioned signals (semantic recall + path-scope match + keyword overlap), then
asks the LLM to judge whether the diff violates any of them. On conflict,
surfaces it via github.post_or_print (PR comment if GH_TOKEN set, else terminal).

Usage:
  python contradiction.py --repo demo_repo --branch violation
  python contradiction.py --repo demo_repo --branch benign      # control
  python contradiction.py --repo demo_repo --head <sha>         # a specific commit
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys

from rich.console import Console
from rich.panel import Panel

import cognee_client
import registry
from config import DEMO_REPO, check_keys
from git_io import diff_of_branch, diff_of_commit
from llm import judge_contradiction

console = Console()

_STOP = set("a an the and or to in of for on with is are be this that from by as it its our we do not no must go all".split())


def _keywords(text: str) -> set[str]:
    return {w for w in re.findall(r"[A-Za-z_][A-Za-z0-9_./-]+", text.lower()) if w not in _STOP and len(w) > 3}


def hybrid_retrieval(diff: str, touched_files: list[str]) -> list[str]:
    """Three signals, unioned, so a single recall miss can't sink the demo.

    1. semantic recall — query built from diff text + touched files
    2. path-scope match — registry entries whose scope overlaps touched paths
    3. keyword overlap — registry entries whose decision keywords appear in the diff
    Returns de-duplicated decision texts to feed the judge.
    """
    chosen: list[str] = []
    seen: set[str] = set()

    def add(text: str) -> None:
        key = text.strip().lower()[:120]
        if key and key not in seen:
            seen.add(key)
            chosen.append(text)

    # 2 + 3 are synchronous and registry-local; do them first so the judge has
    # material even if recall is empty.
    diff_lower = diff.lower()
    diff_kw = _keywords(diff)
    for entry in registry.all_active():
        # path-scope
        scope = (entry.get("scope") or "").lower()
        scope_tokens = [t.strip() for t in scope.replace(",", " ").split() if t.strip()]
        if any(any(tok in tf.lower() for tf in touched_files) for tok in scope_tokens):
            add(_entry_text(entry))
            continue
        # keyword overlap between decision text and diff
        dec_kw = _keywords(entry.get("decision", "") + " " + entry.get("rationale", ""))
        if len(dec_kw & diff_kw) >= 2:
            add(_entry_text(entry))

    # 1. semantic recall (async, done by caller) — query from diff keywords + paths
    return chosen


def _entry_text(entry: dict) -> str:
    return (f"Decision: {entry.get('decision','')}\n"
            f"Rationale: {entry.get('rationale','')}\n"
            f"Scope: {entry.get('scope','')}\n"
            f"Source commit: {(entry.get('sha') or '')[:8]}")


async def detect(repo_path: str, *, branch: str | None, head: str | None,
                 base: str | None = None, post_comment: bool = True) -> dict:
    check_keys(need_cognee=True, need_llm=True)
    await cognee_client.connect()

    if base and head:
        # PR range: explicit base..head SHAs (CI passes github.event.pull_request.base/head).
        diff, touched = diff_of_branch(repo_path, base=base, head=head)
        sha = head
    elif head:
        diff, touched = diff_of_commit(repo_path, head)
        sha = head
    else:
        diff, touched = diff_of_branch(repo_path, base="main", head=branch or "HEAD")
        sha = branch or "HEAD"

    console.print(f"[bold]Diff[/bold] from {sha}, touched: {touched}")

    # local signals first
    local = hybrid_retrieval(diff, touched)
    console.print(f"[blue]local signals:[/blue] {len(local)} candidate decision(s)")
    for d in local:
        console.print(f"  - {d.splitlines()[0][:80]}")

    # semantic recall — query derived from the diff so 'fetch'/'apiClient' surface D1
    query = " ".join(sorted(_keywords(diff))) + " " + " ".join(touched)
    recalled = await cognee_client.recall_decisions(query[:500], top_k=10)
    console.print(f"[blue]semantic recall:[/blue] {len(recalled)} result(s)")
    for r in recalled:
        console.print(f"  - {r[:80]}")

    candidates = list(local)
    seen = {c.lower()[:120] for c in candidates}
    for r in recalled:
        k = r.lower()[:120]
        if k and k not in seen:
            seen.add(k)
            candidates.append(r)

    if not candidates:
        console.print("[yellow]No relevant memories found — nothing to contradict.[/yellow]")
        await cognee_client.disconnect()
        return {"conflict": False, "decision_violated": "", "explanation": "No relevant memories.", "confidence": 1.0}

    console.print(f"\n[bold]Judging {len(candidates)} candidate(s) against the diff...[/bold]")
    verdict = judge_contradiction(diff, candidates)
    console.print(Panel.fit(
        f"conflict: {verdict['conflict']}\n"
        f"decision_violated: {verdict['decision_violated'][:100]}\n"
        f"explanation: {verdict['explanation']}\n"
        f"confidence: {verdict['confidence']}",
        title="Verdict", style="red" if verdict["conflict"] else "green",
    ))

    if verdict["conflict"]:
        # post_or_print is sync; import here to avoid module-level requests import cost
        import github
        github.post_or_print(verdict, sha=sha, post_comment=post_comment)

    await cognee_client.disconnect()
    return verdict


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=str(DEMO_REPO))
    ap.add_argument("--branch", default=None)
    ap.add_argument("--head", default=None)
    ap.add_argument("--base", default=None,
                    help="base SHA for a PR-range diff (CI); paired with --head")
    ap.add_argument("--no-post", action="store_true",
                    help="write pending_conflict.json but skip the PR comment "
                         "(used by the reconcile workflow's re-derive step)")
    args = ap.parse_args()
    if not args.branch and not args.head:
        sys.exit("Provide --branch <name> or --head <sha> (or --base + --head for a PR range)")
    repo = args.repo if os.path.isabs(args.repo) else os.path.join(os.getcwd(), args.repo)
    asyncio.run(detect(repo, branch=args.branch, head=args.head,
                       base=args.base, post_comment=not args.no_post))


if __name__ == "__main__":
    main()