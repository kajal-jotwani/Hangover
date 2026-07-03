"""Phase 3 — the reconciliation loop (the thesis of the project).

  confirm <id>  — the change is intentional. The old belief is revised:
                  remember() an explicit 'UPDATE: superseded' memory (higher
                  importance), surgically forget() the old memory by data_id,
                  improve() to re-weight the graph. The old belief is crossed
                  out; the new one is in. Then re-query to prove the answer changed.

  reject  <id>  — the change is a bug. NO memory change: the old belief was
                  correct and the diff was wrong. This branch is just as
                  important to demo — it's the 'caught a real mistake' beat.

<id> is optional; if omitted, reads the latest pending_conflict.json.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time

from rich.console import Console
from rich.panel import Panel

import cognee_client
import registry
from config import PENDING_CONFLICT_PATH, check_keys

console = Console()


def _load_conflict(arg_id: str | None) -> dict:
    data = json.loads(PENDING_CONFLICT_PATH.read_text())
    return data


async def confirm(conflict: dict, *, reason: str, query: str | None = None, ci: bool = False) -> None:
    check_keys(need_cognee=True, need_llm=False)
    await cognee_client.connect()

    # The proof query makes the before/after delta legible. Default: derived from
    # the violated decision (works for any repo in CI). Override with --query for
    # the on-camera demo's crisp contrastive flip ("can I use an in-memory Map
    # cache instead of Redis?").
    proof_query = query or _proof_query_for(conflict)

    old_decision = conflict.get("decision_violated", "")
    entry = registry.find_by_decision_text(old_decision)
    if not entry:
        console.print(f"[red]Could not match violated decision to a registry entry:[/red]\n  {old_decision}")
        console.print("[yellow]Proceeding to remember the update anyway, but no old memory to forget.[/yellow]")

    # Seed the seen-id set with existing data_ids so remember_decision() can
    # isolate the NEW superseded memory's id by diffing (remember() returns the
    # full items list, not in insertion order).
    existing_ids = {
        v.get("data_id") for v in registry.load_registry().values()
        if isinstance(v, dict) and v.get("data_id")
    }
    cognee_client.seed_seen(existing_ids)
    console.print(f"[dim]Seeded seen-set with {len(existing_ids)} existing data_ids.[/dim]")

    date = time.strftime("%Y-%m-%d")
    new_text = (
        f"UPDATE as of {date}: Previous decision \"{old_decision}\" is SUPERSEDED. "
        f"New behavior: the change is intentional and permitted. "
        f"Reason for change: {reason}."
    )
    console.print(f"[bold]remember()[/bold] the update (higher importance)...")
    res = await cognee_client.remember_decision(new_text, importance_weight=0.95)
    new_id = f"UPDATE-{date}-{(res['data_id'] or 'noid')[:8]}"
    registry.add_entry(
        decision_id=new_id,
        data_id=res["data_id"], sha="",
        decision=f"UPDATE: {old_decision} superseded — {reason}",
        rationale=reason, scope="global", importance=0.95,
    )
    registry.append_event("remember", decision_id=new_id, decision=new_text, data_id=res["data_id"])

    if entry and entry.get("data_id"):
        old_id = entry["data_id"]
        console.print(f"[bold]forget()[/bold] old memory by data_id={old_id} (surgical)...")
        try:
            await cognee_client.forget_one(old_id)
            registry.upsert_entry(_id_for(entry), status="superseded")
            registry.append_event("forget", decision_id=_id_for(entry), data_id=old_id, superseded_by=new_id)
            console.print("  [green]old belief crossed out.[/green]")
        except Exception as e:
            console.print(f"  [red]forget failed: {e}[/red]")
            console.print("  [yellow]Fallback: old memory remains but the UPDATE has higher importance; improve() will re-weight.[/yellow]")
    else:
        console.print("[yellow]No data_id for old memory — relying on improve() re-weighting toward the UPDATE.[/yellow]")

    console.print(f"[bold]improve()[/bold] re-weighting the graph...")
    try:
        await cognee_client.improve_graph()
        registry.append_event("improve", note="re-weighted after update")
    except Exception as e:
        console.print(f"  [dim]improve: {e}[/dim]")

    console.print(Panel.fit("Belief updated. Old memory forgotten, new one remembered, graph re-weighted.",
                            title="confirm", style="green"))

    # The proof moment: re-query so the before/after delta is legible.
    # Before: 'no, must use Redis'; after: 'yes, in-memory Map permitted as of
    # the update'. That before/after flip is the demo's spine.
    console.print(f"\n[bold]PROOF — recall({proof_query!r}):[/bold]")
    answers = await cognee_client.recall_decisions(proof_query)
    for a in answers:
        console.print(f"  - {a[:240]}")
    if ci:
        _post_after_comment("confirm", conflict, reason, proof_query, answers)
        # The conflict is resolved — flip the commit status from failure → success.
        import github
        github.post_commit_status(conflict.get("sha", ""),
                                  "success", "Memory updated — old belief crossed out")
    await cognee_client.disconnect()


async def reject(conflict: dict, *, query: str | None = None, ci: bool = False) -> None:
    check_keys(need_cognee=True, need_llm=False)
    await cognee_client.connect()
    console.print("[bold]reject[/bold] — the diff is a bug. NO memory change.")
    console.print(f"The old belief stands: [cyan]{conflict.get('decision_violated','')[:120]}[/cyan]")
    registry.append_event("reject", decision_violated=conflict.get("decision_violated", ""),
                          note="change rejected as a bug; memory unchanged")
    proof_query = query or _proof_query_for(conflict)
    console.print(f"\n[bold]Memory unchanged — recall({proof_query!r}):[/bold]")
    answers = await cognee_client.recall_decisions(proof_query)
    for a in answers:
        console.print(f"  - {a[:240]}")
    if ci:
        _post_after_comment("reject", conflict, "", proof_query, answers)
        # The bug branch: open a GitHub issue tracking the caught regression, and
        # keep the commit status red so it blocks merge.
        import github
        dec = conflict.get("decision_violated", "").replace("\n", " ")[:200]
        sha = conflict.get("sha", "")[:12]
        github.create_issue(
            f"CodeMind caught a likely regression: {dec[:90]}",
            f"CodeMind flagged a PR change as contradicting a past decision and a "
            f"maintainer rejected it as a bug.\n\n"
            f"**Violated decision:** {dec}\n\n"
            f"**Why CodeMind flagged it:** {conflict.get('explanation','')[:400]}\n\n"
            f"**Source commit:** {sha}\n\n"
            f"The old belief stands — the code change should be reverted or fixed.",
            labels=["codemind", "regression"],
        )
        github.post_commit_status(conflict.get("sha", ""),
                                   "failure", "Rejected as a bug — issue opened")
    await cognee_client.disconnect()


def _id_for(entry: dict) -> str:
    # Reverse-lookup the registry key for an entry dict.
    reg = registry.load_registry()
    for k, v in reg.items():
        if v is entry or v.get("data_id") == entry.get("data_id"):
            return k
    return entry.get("data_id", "unknown")


def _proof_query_for(conflict: dict) -> str:
    """Default proof query: ask whether the violated decision still holds.
    Generic (works for any repo in CI); the demo overrides via --query."""
    dec = (conflict.get("decision_violated") or "").strip().replace("\n", " ")
    return f"Is the following decision still in force, or has it been superseded? {dec[:300]}"


def _post_after_comment(action: str, conflict: dict, reason: str,
                         query: str, answers: list[str]) -> None:
    """In CI, surface the after-recall result as a PR comment (the loop-closing beat)."""
    import github
    dec = conflict.get("decision_violated", "")
    head = ("✅" if action == "confirm" else "⛔")
    title = "memory updated — old belief crossed out" if action == "confirm" \
        else "memory unchanged — change rejected as a bug"
    lines = [f"{head} **CodeMind: {title}.**", ""]
    if action == "confirm":
        lines.append(f"Reason: {reason}")
        lines.append(f"Old decision: {dec[:200]}")
        lines.append("")
    lines.append(f"Re-asked: _{query[:160]}_")
    lines.append("")
    lines.append("Recall now returns:")
    if answers:
        for a in answers[:5]:
            lines.append(f"- {a[:240]}")
    else:
        lines.append("- (no recall — graph may still be settling)")
    github.post_result_comment("\n".join(lines))


async def _run(action: str, conflict_id: str | None, reason: str,
               query: str | None, ci: bool) -> None:
    conflict = _load_conflict(conflict_id)
    if action == "confirm":
        await confirm(conflict, reason=reason, query=query, ci=ci)
    else:
        await reject(conflict, query=query, ci=ci)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="action", required=True)
    c = sub.add_parser("confirm")
    c.add_argument("id", nargs="?", default=None)
    c.add_argument("--reason", default="intentional change, rationale updated")
    r = sub.add_parser("reject")
    r.add_argument("id", nargs="?", default=None)
    for p in (c, r):
        p.add_argument("--query", default=None,
                       help="override the proof recall query (demo uses the contrastive form)")
        p.add_argument("--ci", action="store_true",
                       help="post the after-recall result as a PR comment (CI mode)")
    args = ap.parse_args()
    asyncio.run(_run(args.action, args.id, getattr(args, "reason", ""),
                     getattr(args, "query", None), getattr(args, "ci", False)))


if __name__ == "__main__":
    main()