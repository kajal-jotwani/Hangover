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


class TestMonorepoScaling(unittest.TestCase):
    """Per-subsystem retrieval + focused diff — the helpers that make detection
    scale to large monorepo PRs. Pure logic, no network."""

    def test_split_diff_by_file_isolates_each_file_hunk(self):
        from contradiction import _split_diff_by_file
        diff = (
            "diff --git a/services/payments/charge.ts b/services/payments/charge.ts\n"
            "+new Map() instead of redis\n"
            "diff --git a/services/auth/login.ts b/services/auth/login.ts\n"
            "+direct fetch() call\n"
        )
        hunks = _split_diff_by_file(diff)
        self.assertEqual(set(hunks), {"services/payments/charge.ts", "services/auth/login.ts"})
        self.assertIn("redis", hunks["services/payments/charge.ts"])
        self.assertIn("fetch", hunks["services/auth/login.ts"])
        # and they don't bleed into each other
        self.assertNotIn("fetch", hunks["services/payments/charge.ts"])

    def test_file_group_top_two_segments(self):
        from contradiction import _file_group
        self.assertEqual(_file_group("services/payments/charge.ts"), "services/payments")
        self.assertEqual(_file_group("services/auth/login.ts"), "services/auth")
        # shallow (flat-repo) files group by their full path — one group per file,
        # which is the right granularity for a non-monorepo layout
        self.assertEqual(_file_group("cache.ts"), "cache.ts")

    def test_group_hunks_collapses_files_to_subsystems(self):
        from contradiction import _group_hunks, _split_diff_by_file
        diff = (
            "diff --git a/services/payments/charge.ts b/services/payments/charge.ts\n+x\n"
            "diff --git a/services/payments/refund.ts b/services/payments/refund.ts\n+y\n"
            "diff --git a/services/auth/login.ts b/services/auth/login.ts\n+z\n"
        )
        groups = _group_hunks(_split_diff_by_file(diff))
        self.assertEqual(set(groups), {"services/payments", "services/auth"})
        # two payments files collapsed into one subsystem hunk
        self.assertIn("charge.ts", groups["services/payments"] if "charge" in groups["services/payments"] else groups["services/payments"])

    def test_heaviest_groups_caps_and_orders_by_diff_size(self):
        from contradiction import _group_hunks, _heaviest_groups
        groups = {"a/a": "x\n" * 50, "b/b": "y\n" * 5, "c/c": "z\n" * 30}
        out = _heaviest_groups(groups, cap=2)
        self.assertEqual(out, ["a/a", "c/c"])  # heaviest first, capped at 2

    def test_focused_diff_puts_relevant_files_first(self):
        from contradiction import _split_diff_by_file, _focused_diff
        diff = (
            "diff --git a/services/payments/charge.ts b/services/payments/charge.ts\n"
            "+REDIS_VIOLATION_MARKER\n"
            "diff --git a/services/auth/login.ts b/services/auth/login.ts\n"
            "+unrelated auth change that pads the diff a lot " + "x" * 200 + "\n"
        )
        hunks = _split_diff_by_file(diff)
        # with a tight cap, the relevant (payments) file must survive truncation
        focused = _focused_diff(hunks, ["services/payments/charge.ts"], cap=400)
        self.assertIn("REDIS_VIOLATION_MARKER", focused,
                       "relevant file must be ordered first so the judge's truncation keeps it")
        # with no relevant files, falls back to file order (still no crash)
        self.assertTrue(_focused_diff(hunks, []))

    def test_focused_diff_empty_when_no_hunks(self):
        from contradiction import _focused_diff
        self.assertEqual(_focused_diff({}, ["anything"]), "")

    def test_focused_diff_surfaces_high_deletion_files_first(self):
        """A revert's heavily-deleted CODE file must beat a low-deletion data
        file when both are relevant, so the judge sees the code revert, not a
        sea of bin/json drops. This is the fix that let the audit catch the
        real cognee token-usage + retry contradictions."""
        from contradiction import _split_diff_by_file, _focused_diff
        diff = (
            "diff --git a/eval/results/dense.json b/eval/results/dense.json\n"
            + "".join(f"-data line {i}\n" for i in range(3)) +  # 3 deletions (noise)
            "diff --git a/eval/measure.py b/eval/measure.py\n"
            + "".join(f"-def func_{i}(): pass\n" for i in range(20)) +  # 20 deletions (real code)
            "diff --git a/eval/plot.py b/eval/plot.py\n"
            "+unrelated padding " + "x" * 200 + "\n"
        )
        hunks = _split_diff_by_file(diff)
        # both .json and .py are "relevant"; under a tight cap the high-deletion
        # .py must come first so the judge sees the removed code.
        focused = _focused_diff(hunks,
                                ["eval/results/dense.json", "eval/measure.py", "eval/plot.py"],
                                cap=300)
        self.assertIn("def func_0", focused,
                       "high-deletion code file must surface before low-deletion data file")


class TestScopeMatchedFiles(_TempRegistryMixin, unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmpdir = tempfile.mkdtemp()
        self._use_temp_registry({
            "D2": {"status": "active", "decision": "Cache layer must be Redis",
                   "scope": "src/cache, services/cache", "importance": 0.98},
        })

    def test_scope_match_surfaces_files_under_decision(self):
        from contradiction import _scope_matched_files
        matched = _scope_matched_files(["src/cache/index.ts", "src/log.ts", "services/cache/store.ts"])
        self.assertEqual(matched, ["src/cache/index.ts", "services/cache/store.ts"])

    def test_no_scope_no_match(self):
        from contradiction import _scope_matched_files
        self.assertEqual(_scope_matched_files(["src/log.ts", "README.md"]), [])


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