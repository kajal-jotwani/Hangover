"""STRETCH — build a self-contained dashboard HTML from the local state files.

  python dashboard/build.py  ->  dashboard/index.html (open in a browser)

Renders:
  - Current beliefs (active vs superseded-crossed-out) with importance scores
  - A 'belief changed' timeline from event_log.json (remember / forget / improve / reject)
"""
from __future__ import annotations

import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import EVENT_LOG_PATH, REGISTRY_PATH

HERE = Path(__file__).resolve().parent
OUT = HERE / "index.html"


def _read(p: Path, default):
    return json.loads(p.read_text()) if p.exists() else default


def build() -> None:
    reg = _read(REGISTRY_PATH, {})
    events = _read(EVENT_LOG_PATH, [])
    active = [e for e in reg.values() if e.get("status") == "active"]
    superseded = [e for e in reg.values() if e.get("status") == "superseded"]

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
</style></head><body>
<h1>CodeMind — the repo that remembers</h1>
<p style="color:#9ca3af">Shared memory graph on Cognee Cloud. This is a snapshot of <code>memory_registry.json</code> + <code>event_log.json</code>.</p>

<h2>Current beliefs ({len(active)} active, {len(superseded)} superseded)</h2>
<div class="grid">{''.join(card(e) for e in active) or '<div class="empty">No memories yet — run ingest.py</div>'}
{''.join(card(e) for e in superseded)}</div>

<h2>Belief-changed timeline ({len(events)} events)</h2>
<div>{timeline or '<div class="empty">No events yet.</div>'}</div>

</body></html>"""
    OUT.write_text(doc)
    print(f"Wrote {OUT}  ({len(active)} active, {len(superseded)} superseded, {len(events)} events)")
    print(f"Open:  file://{OUT}")


if __name__ == "__main__":
    build()