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

# Monorepo-scale retrieval bounds. Per PR we run one retrieval query per
# SUBSYSTEM (top-2 path segments) touched — not one per file, and not one
# PR-wide — so a 50-file PR across 8 packages becomes 8 focused queries
# instead of a single alphabetically-truncated one. See detect() below.
_MAX_GROUPS = 20        # cap distinct subsystems we'll query per PR
_TOP_K_PER_GROUP = 5    # recall + graph nodes per subsystem query
_MAX_CANDIDATES = 25    # total deduped candidates fed to the judge
_MAX_GRAPH_NODES = 12  # nodes cited in the PR comment


def _keywords(text: str) -> set[str]:
    return {w for w in re.findall(r"[A-Za-z_][A-Za-z0-9_./-]+", text.lower()) if w not in _STOP and len(w) > 3}


def _file_group(path: str) -> str:
    """Subsystem key for a file path = top-2 path segments (or top-1 if shallow).

    services/payments/checkout.ts -> "services/payments"; cache.ts -> "cache".
    This is the unit we run retrieval against, so a monorepo PR is queried per
    touched subsystem rather than per file or PR-wide.
    """
    parts = path.split("/")
    return "/".join(parts[:2]) if len(parts) >= 2 else (parts[0] or "root")


def _split_diff_by_file(diff: str) -> dict[str, str]:
    """Split a unified git diff into {file_path: that file's hunk text}.

    Per-file hunks are the unit a past decision applies to; building retrieval
    queries per subsystem-of-files (instead of one PR-wide) is what keeps
    detection accurate on large monorepo PRs.
    """
    hunks: dict[str, str] = {}
    cur_file: str | None = None
    cur: list[str] = []
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            if cur_file is not None:
                hunks[cur_file] = "\n".join(cur)
            parts = line.split(" b/", 1)
            cur_file = parts[1].strip() if len(parts) == 2 else None
            cur = [line]
        elif cur_file is not None:
            cur.append(line)
    if cur_file is not None:
        hunks[cur_file] = "\n".join(cur)
    return hunks


def _group_hunks(file_hunks: dict[str, str]) -> dict[str, str]:
    """Group per-file hunks by subsystem -> combined hunk text.

    Bounds the number of retrieval queries to O(subsystems touched) rather than
    O(files touched): a 200-file PR across 8 services issues 8 queries, not 200.
    """
    groups: dict[str, list[str]] = {}
    for f, h in file_hunks.items():
        groups.setdefault(_file_group(f), []).append(h)
    return {k: "\n".join(vs) for k, vs in groups.items()}


def _heaviest_groups(group_hunks: dict[str, str], cap: int = _MAX_GROUPS) -> list[str]:
    """Subsystem keys with the most diff lines first, capped so a pathological
    PR can't explode the cloud-call count."""
    return [k for k, _ in sorted(group_hunks.items(),
            key=lambda kv: len(kv[1].splitlines()), reverse=True)[:cap]]


def _scope_matched_files(touched_files: list[str]) -> list[str]:
    """Touched files that fall under an active registry decision's scope.

    The deterministic path-scope signal — tells the focused-diff builder which
    files to surface to the judge even when semantic recall is silent, so a
    recall miss can't hide a decision that applies by path.
    """
    out: list[str] = []
    for tf in touched_files:
        tf_l = tf.lower()
        for entry in registry.all_active():
            scope = (entry.get("scope") or "").lower()
            if not scope:
                continue
            toks = [t.strip() for t in scope.replace(",", " ").split() if t.strip()]
            if any(tok in tf_l for tok in toks):
                out.append(tf)
                break
    return out


def _focused_diff(file_hunks: dict[str, str], relevant_files: list[str],
                 cap: int = 8000) -> str:
    """Concatenate per-file hunks with relevant files first, capped at `cap`.

    The judge truncates to `cap` chars; ordering relevant files first means that
    truncation keeps the decision-relevant hunks, not arbitrary
    alphabetically-early ones — the same fix as the retrieval query, applied to
    what the judge actually sees.
    """
    ordered: list[str] = []
    seen: set[str] = set()
    for f in relevant_files:
        h = file_hunks.get(f)
        if h is not None and f not in seen:
            ordered.append(h)
            seen.add(f)
    for f, h in file_hunks.items():
        if f not in seen:
            ordered.append(h)
            seen.add(f)
    return "\n".join(ordered)[:cap] if ordered else ""


async def _recall_for_group(group: str, hunk: str) -> tuple[list[str], list[str]]:
    """One focused retrieval query per subsystem: recall + graph nodes.

    Query = keywords from this subsystem's hunks + the subsystem path, so the
    query is scoped to the subsystem's vocabulary instead of the whole PR's
    (which, on a big monorepo PR, would be an arbitrary 500-char slice of every
    subsystem's keywords mashed together).
    """
    query = (" ".join(sorted(_keywords(hunk))) + " " + group)[:500]
    recalled = await cognee_client.recall_decisions(query, top_k=_TOP_K_PER_GROUP)
    nodes = await cognee_client.search_graph_nodes(query, top_k=_TOP_K_PER_GROUP)
    return recalled, nodes


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

    console.print(f"[bold]Diff[/bold] from {sha}, {len(touched)} file(s) touched")

    # Per-file hunks + subsystem grouping — the unit a past decision applies to.
    # Splitting retrieval by subsystem (not one PR-wide query) is what keeps
    # detection accurate on large monorepo PRs: a 50-file PR across 8 packages
    # becomes 8 focused queries instead of one alphabetically-truncated one.
    file_hunks = _split_diff_by_file(diff)
    group_hunks = _group_hunks(file_hunks)
    groups = _heaviest_groups(group_hunks)
    console.print(f"[blue]subsystems:[/blue] querying {len(groups)} group(s) "
                  f"({len(file_hunks)} file(s) -> {len(group_hunks)} subsystems)")

    # local signals first (path-scope + keyword overlap) — deterministic safety net
    local = hybrid_retrieval(diff, touched)
    scope_files = _scope_matched_files(touched)
    console.print(f"[blue]local signals:[/blue] {len(local)} candidate(s) "
                  f"({len(scope_files)} scope-matched file(s))")
    for d in local:
        console.print(f"  - {d.splitlines()[0][:80]}")

    # per-subsystem semantic + graph retrieval (bounded + concurrent). Each group
    # gets its own focused query, so no subsystem's vocabulary is drowned out by
    # another's — the core monorepo fix.
    per_group = await asyncio.gather(*[_recall_for_group(g, group_hunks[g]) for g in groups])
    recalled: list[str] = []
    graph_nodes: list[str] = []
    relevant_files: set[str] = set(scope_files)
    file_to_group = {f: _file_group(f) for f in file_hunks}
    for g, (rec, nodes) in zip(groups, per_group):
        if rec or nodes:
            # every file under a hit subsystem is decision-relevant for the focused diff
            relevant_files.update(f for f, fg in file_to_group.items() if fg == g)
        recalled.extend(rec)
        graph_nodes.extend(nodes)
    console.print(f"[blue]semantic recall:[/blue] {len(recalled)} result(s) "
                  f"across {len(groups)} subsystem query(ies)")
    console.print(f"[blue]graph nodes:[/blue] {len(graph_nodes)} node(s)")
    for n in graph_nodes[:3]:
        console.print(f"  - {n[:80]}")

    # union + de-dupe + cap (so a noisy large graph can't drown the judge)
    candidates = list(local)
    seen = {c.lower()[:120] for c in candidates}
    for r in recalled + graph_nodes:
        k = r.lower()[:120]
        if k and k not in seen:
            seen.add(k)
            candidates.append(r)
    candidates = candidates[:_MAX_CANDIDATES]
    graph_nodes = graph_nodes[:_MAX_GRAPH_NODES]

    if not candidates:
        console.print("[yellow]No relevant memories found — nothing to contradict.[/yellow]")
        if post_comment:
            import github
            github.post_commit_status(sha, "success", "No relevant memories — nothing to contradict")
        await cognee_client.disconnect()
        return {"conflict": False, "decision_violated": "", "explanation": "No relevant memories.", "confidence": 1.0}

    # focused diff: relevant files first so the judge's truncation keeps the
    # decision-relevant hunks, not arbitrary alphabetically-early ones.
    focused = _focused_diff(file_hunks, sorted(relevant_files)) or diff
    console.print(f"\n[bold]Judging {len(candidates)} candidate(s) against "
                  f"{len(relevant_files)} relevant file(s)...[/bold]")
    verdict = judge_contradiction(focused, candidates)
    # Attach the graph nodes that informed the verdict so the PR comment can cite them.
    verdict["graph_nodes"] = graph_nodes
    console.print(Panel.fit(
        f"conflict: {verdict['conflict']}\n"
        f"decision_violated: {verdict['decision_violated'][:100]}\n"
        f"explanation: {verdict['explanation']}\n"
        f"confidence: {verdict['confidence']}",
        title="Verdict", style="red" if verdict["conflict"] else "green",
    ))

    if verdict["conflict"]:
        # post_or_print is sync; import here to avoid module-level requests cost
        import github
        github.post_or_print(verdict, sha=sha, post_comment=post_comment)
        entry = registry.find_by_decision_text(verdict.get("decision_violated", ""))
        if entry:
            decision_id = next((k for k, v in registry.load_registry().items() if v is entry), "")
            registry.append_event(
                "contradiction",
                sha=sha,
                decision_violated=verdict.get("decision_violated", ""),
                decision_id=decision_id,
                data_id=entry.get("data_id", ""),
                confidence=verdict.get("confidence", 0.0),
            )
    elif post_comment:
        # Clean PR: post a green check so CodeMind always shows up in the PR check
        # summary (green on clean, red on conflict). Lets the check be a *required*
        # status check that blocks merge on conflict but not on clean PRs.
        import github
        github.post_commit_status(sha, "success", "No contradiction with past decisions")

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