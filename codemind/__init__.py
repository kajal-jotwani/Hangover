"""CodeMind CLI package.

Editable installs only expose ``codemind*`` on sys.path, but several runtime
modules (config, cognee_client, ingest, registry, ...) live at the repo root
as siblings of this package. Add the repo root to sys.path so the CLI works
when invoked from any directory, not just from inside this repo.
"""
import sys as _sys
from pathlib import Path as _Path

_REPO_ROOT = str(_Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in _sys.path:
    _sys.path.insert(0, _REPO_ROOT)