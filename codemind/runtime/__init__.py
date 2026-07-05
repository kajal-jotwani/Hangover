"""CodeMind CI runtime.

These modules implement the actual memory lifecycle the workflows run:
``contradiction.py`` (PR detection), ``reconcile.py`` (confirm/reject loop),
``ingest.py`` (auto-ingest on merge), plus their shared helpers
(``config``, ``cognee_client``, ``registry``, ``llm``, ``git_io``, ``github``).

They are written to run in two contexts:

1. **Inside the installed ``codemind`` package** — the CLI imports them as
   ``from codemind.runtime import contradiction`` etc. Intra-runtime imports
   use the package-relative form ``from codemind.runtime.config import ...``.
2. **Vendored standalone in a target repo's ``.codemind/``** —
   ``codemind init`` copies these files there and rewrites those package-
   relative imports back to bare sibling imports (``from config import ...``)
   so the workflows can run ``python .codemind/contradiction.py`` with
   ``.codemind/`` on ``sys.path`` and no ``codemind`` package installed.

``vendor_codemind`` in ``codemind.onboarding`` owns that rewrite.
"""