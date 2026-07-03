"""STRETCH — build a self-contained dashboard HTML from local state + live Cognee.

  python dashboard/build.py  ->  dashboard/index.html (open in a browser)

Renders:
  - Current beliefs (active vs superseded-crossed-out) with importance scores
  - A 'belief changed' timeline from event_log.json (remember / forget / improve / reject)
  - LIVE Cognee memory graph nodes pulled via cognee.search(only_context=True)
    (cognee.visualize() is blocked on the cloud tenant by a broken local-SQLite
    path, so we render the graph content ourselves from the real retrieved nodes)
  - A Cognee lifecycle-API footprint badge (which verbs the project leans on)
"""
from __future__ import annotations

import asyncio
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import EVENT_LOG_PATH, REGISTRY_PATH, check_keys  # noqa: F401  (check_keys used in try)

HERE = Path(__file__).resolve().parent
OUT = HERE / "index.html"

# The Cognee lifecycle APIs CodeMind leans on — surfaced as a badge so judges
# can see the footprint at a glance. (memify/improve are best-effort on cloud.)
LIFECYCLE = [
    ("remember", "remember() — extracts decision facts from each commit"),
    ("recall", "recall() — retrieves relevant past decisions for a diff"),
    ("search", "search(only_context=True) — pulls the actual graph nodes"),
    ("forget", "forget(data_id) — surgically retires a single superseded memory"),
    ("improve", "improve() / self_improvement — re-weights after a confirmed update"),
]


def _read(p: Path, default):
    return json.loads(p.read_text()) if p.exists() else default


async def _live_nodes() -> list[str]:
    """Pull the actual Cognee graph nodes via cognee.search(only_context=True)."""
    import cognee_client
    from config import check_keys
    check_keys(need_cognee=True)
    await cognee_client.connect()
    try:
        # A broad query so we surface as many remembered decisions as possible.
        nodes = await cognee_client.search_graph_nodes(
            "engineering decisions cache redis apiClient logger regex fetch", top_k=20)
    finally:
        await cognee_client.disconnect()
    return nodes


def build() -> None:
    reg = _read(REGISTRY_PATH, {})
    events = _read(EVENT_LOG_PATH, [])
    active = [e for e in reg.values() if e.get("status") == "active"]
    superseded = [e for e in reg.values() if e.get("status") == "superseded"]

    # Try to pull live Cognee graph nodes (best-effort; skip if no creds/offline).
    live_nodes: list[str] = []
    live_err = ""
    try:
        live_nodes = asyncio.run(_live_nodes())
    except SystemExit as e:
        live_err = str(e)
    except Exception as e:
        live_err = f"{type(e).__name__}: {e}"

    def card(e):
        imp = e.get("importance", "?")
        dec = html.escape(e.get("decision", "")[:140])
        rat = html.escape(e.get("rationale", "")[:200])
        scope = html.escape(e.get("scope", "")[:80])
        return f"""<div class="card {'super' if e.get('status')=='superseded' else 'active'}">
          <div class="imp">importance {imp}</div>
          <div class="dec">{dec}</div>
          <div class="rat">{rat}</div>
          <div class="scope">scope: {scope}</div>
        </div>"""

    timeline = ""
    for ev in reversed(events):
        kind = ev.get("kind", "")
        color = {"remember": "#3b82f6", "forget": "#ef4444", "improve": "#a855f7",
                 "reject": "#f59e0b"}.get(kind, "#6b7280")
        body = html.escape(json.dumps({k: v for k, v in ev.items() if k not in ("ts", "kind")}, ensure_ascii=False)[:160])
        timeline += f'<div class="ev"><span class="dot" style="background:{color}"></span><b>{kind}</b> {body}</div>'

    # Lifecycle footprint badges
    badges = " ".join(
        f'<span class="badge" title="{html.escape(d)}">{n}</span>' for n, d in LIFECYCLE
    )

    # Live Cognee graph nodes section
    if live_err:
        live_html = f'<div class="empty">Live Cognee graph unavailable: {html.escape(live_err)}</div>'
    elif live_nodes:
        nodes_html = "".join(
            f'<div class="node">{html.escape(n[:300])}</div>' for n in live_nodes[:12]
        )
        live_html = nodes_html
    else:
        live_html = '<div class="empty">No graph nodes retrieved (graph may be empty).</div>'

    doc = f"""<!doctype html><html><head><meta charset="utf-8">
<title>CodeMind — Memory Graph</title>
<style>
  body{{font-family:-apple-system,system-ui,sans-serif;background:#0b1020;color:#e5e7eb;margin:0;padding:24px}}
  h1{{font-size:22px}} h2{{font-size:16px;color:#9ca3af;margin-top:28px;border-bottom:1px solid #1f2937;padding-bottom:6px}}
  .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px}}
  .card{{background:#151c30;border:1px solid #243049;border-radius:10px;padding:14px;position:relative}}
  .card.super{{opacity:.5}} .card.super .dec{{text-decoration:line-through;color:#9ca3af}}
  .imp{{font-size:11px;color:#60a5fa;float:right}}
  .dec{{font-weight:600;margin-bottom:6px}} .rat{{font-size:13px;color:#cbd5e1}} .scope{{font-size:11px;color:#64748b;margin-top:8px}}
  .ev{{font-size:13px;padding:6px 0;border-bottom:1px solid #111827;display:flex;gap:10px;align-items:center}}
  .dot{{width:10px;height:10px;border-radius:50%;display:inline-block;flex:none}}
  .empty{{color:#6b7280;font-style:italic}}
  .badge{{display:inline-block;background:#1e293b;border:1px solid #334155;color:#60a5fa;
         padding:3px 10px;border-radius:12px;font-size:12px;margin:2px;font-family:monospace}}
  .node{{background:#0f172a;border-left:3px solid #a855f7;padding:8px 12px;margin:6px 0;
        border-radius:4px;font-size:13px;white-space:pre-wrap;color:#d1d5db}}
</style></head><body>
<h1>CodeMind — the repo that remembers</h1>
<p style="color:#9ca3af">Shared memory graph on Cognee Cloud. Local snapshot from <code>memory_registry.json</code> + <code>event_log.json</code>, plus live graph nodes from <code>cognee.search</code>.</p>

<h2>Cognee lifecycle footprint</h2>
<div style="margin-bottom:8px">{badges}</div>

<h2>Current beliefs ({len(active)} active, {len(superseded)} superseded)</h2>
<div class="grid">{''.join(card(e) for e in active) or '<div class="empty">No memories yet — run ingest.py</div>'}
{''.join(card(e) for e in superseded)}</div>

<h2>Live Cognee memory graph ({len(live_nodes)} nodes via cognee.search only_context)</h2>
<div>{live_html}</div>

<h2>Belief-changed timeline ({len(events)} events)</h2>
<div>{timeline or '<div class="empty">No events yet.</div>'}</div>

</body></html>"""
    OUT.write_text(doc)
    print(f"Wrote {OUT}  ({len(active)} active, {len(superseded)} superseded, "
          f"{len(events)} events, {len(live_nodes)} live nodes)")


if __name__ == "__main__":
    build()