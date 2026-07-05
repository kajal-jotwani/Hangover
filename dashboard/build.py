"""Thin shim — the dashboard builder now lives in the installed package.

  python dashboard/build.py  ->  dashboard/index.html

Kept so existing docs/scripts that invoke this path keep working. The real
implementation is ``codemind.dashboard.build_dashboard`` (shipped in the wheel).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from codemind.dashboard import build_dashboard
from codemind.onboarding import discover_repo_root

if __name__ == "__main__":
    build_dashboard(discover_repo_root())