"""Pure-logic unit tests for CodeMind (no network, no Cognee, no creds).

Run with:  .venv/bin/python -m unittest discover -s tests

Covers the pieces that would be embarrassing to break silently:
  - registry.find_by_decision_text fuzzy match (reconcile uses it to find the
    data_id to forget — a wrong match would forget the wrong memory)
  - contradiction.hybrid_retrieval keyword-overlap signal (the local signal that
    makes CI detection reliable even when semantic recall surfaces junk)
  - github comment idempotency marker + graph-evidence formatting (so re-pushes
    don't spam duplicate comments and the Cognee node is actually cited)
"""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _TempRegistryMixin:
    """Point registry + config at a temp file so tests don't touch the real one."""
    def _use_temp_registry(self, entries: dict) -> Path:
        tmp = Path(self._tmpdir) / "memory_registry.json"
        tmp.write_text(json.dumps(entries))
        import registry
        registry.REGISTRY_PATH = tmp
        return tmp


class TestRegistryFuzzyMatch(_TempRegistryMixin, unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmpdir = tempfile.mkdtemp()
        self._use_temp_registry({
            "D2": {"data_id": "abc-123", "sha": "deadbeef", "status": "active",
                   "decision": "Cache layer must be Redis; never use in-memory Map caches",
                   "rationale": "In-process caches cause stale data", "scope": "src/cache",
                   "importance": 0.98},
            "D1": {"data_id": "xyz-999", "sha": "cafef00d", "status": "active",
                   "decision": "All network calls must use the apiClient wrapper",
                   "rationale": "Retry + auth headers", "scope": "src/api", "importance": 0.9},
        })

    def test_exact_is_match(self):
        import registry
        e = registry.find_by_decision_text("Cache layer must be Redis; never use in-memory Map caches; always use cacheGet/cacheSet")
        self.assertIsNotNone(e)
        self.assertEqual(e["data_id"], "abc-123")  # must map to the RIGHT memory

    def test_loose_word_overlap_matches(self):
        import registry
        e = registry.find_by_decision_text("the cache should use redis not a map")
        self.assertIsNotNone(e)
        self.assertEqual(e["data_id"], "abc-123")

    def test_no_match_returns_none(self):
        import registry
        self.assertIsNone(registry.find_by_decision_text("completely unrelated topic about cooking"))

    def test_skips_superseded(self):
        import registry
        registry.upsert_entry("D2", status="superseded")
        self.assertIsNone(registry.find_by_decision_text("Cache layer must be Redis"))


class TestHybridRetrieval(_TempRegistryMixin, unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmpdir = tempfile.mkdtemp()
        self._use_temp_registry({
            "D2": {"status": "active", "decision": "Cache layer must be Redis; never in-memory Map",
                   "rationale": "In-process caches cause stale data across instances",
                   "scope": "src/cache, scratch/cache.ts", "importance": 0.98, "sha": "deadbeef"},
        })

    def test_keyword_overlap_surfaces_decision(self):
        from contradiction import hybrid_retrieval
        # a diff mentioning cache + redis + map should hit D2 via keyword overlap
        diff = "export const store = new Map(); // switched from Redis cache to in-memory"
        out = hybrid_retrieval(diff, touched_files=["scratch/cache.ts"])
        self.assertTrue(any("Redis" in c for c in out),
                        f"expected Redis decision surfaced, got {out}")

    def test_path_scope_surfaces_decision(self):
        from contradiction import hybrid_retrieval
        out = hybrid_retrieval("unrelated text", touched_files=["src/cache/index.ts"])
        self.assertTrue(any("Redis" in c for c in out))

    def test_unrelated_diff_no_signal(self):
        from contradiction import hybrid_retrieval
        out = hybrid_retrieval("refactor the logger formatting", touched_files=["src/log.ts"])
        self.assertEqual(out, [])


class TestGithubComment(unittest.TestCase):
    def test_marker_is_head_sha_prefix(self):
        from github import _marker
        self.assertEqual(_marker("6a18520fb5cb"), "<!-- codemind:head=6a18520fb5cb -->")
        self.assertEqual(_marker("abc"), "<!-- codemind:head=abc -->")

    def test_format_comment_includes_graph_evidence(self):
        from github import _format_comment
        body = _format_comment(
            {"decision_violated": "Cache layer must be Redis",
             "explanation": "diff replaces Redis with a Map",
             "confidence": 0.9,
             "graph_nodes": ["Decision: Cache layer must be Redis Rationale: stale data"]},
            sha="6a18520fb5cb")
        self.assertIn("Graph evidence", body)
        self.assertIn("Cache layer must be Redis Rationale: stale data", body)
        self.assertIn("<!-- codemind:head=6a18520fb5cb -->", body)

    def test_format_comment_omits_graph_section_when_no_nodes(self):
        from github import _format_comment
        body = _format_comment(
            {"decision_violated": "Cache layer must be Redis", "explanation": "x", "confidence": 0.9},
            sha="abc")
        self.assertNotIn("Graph evidence", body)

    def test_already_commented_detects_marker(self):
        import github
        with patch.dict(os.environ, {"GH_TOKEN": "t", "GH_REPO": "o/r", "GH_PR_NUMBER": "1"}):
            github.GH_TOKEN, github.GH_REPO, github.GH_PR_NUMBER = "t", "o/r", "1"
            fake = MagicMock()
            fake.status_code = 200
            fake.json.return_value = [{"body": "some comment\n<!-- codemind:head=6a18520fb5cb -->"}]
            with patch("github.requests.get", return_value=fake):
                self.assertTrue(github._already_commented("6a18520fb5cbbbbb"))  # marker matches first 12
            fake.json.return_value = [{"body": "a different comment"}]
            with patch("github.requests.get", return_value=fake):
                self.assertFalse(github._already_commented("ffffffffffff"))


if __name__ == "__main__":
    unittest.main(verbosity=2)