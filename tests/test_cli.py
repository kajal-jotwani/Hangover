import asyncio
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from codemind.onboarding import ValidationResult
from codemind.cli import main


class TestCli(unittest.TestCase):
    def _init_git_repo(self, root: Path) -> None:
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)

    def test_init_writes_env_and_workflows_idempotently(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = Path.cwd()
            self._init_git_repo(root)
            with patch("codemind.cli.validate_cognee_credentials", AsyncMock(return_value=ValidationResult(True, "ok"))), \
                 patch("codemind.cli.validate_ollama_credentials", AsyncMock(return_value=ValidationResult(True, "ok"))):
                args = [
                    "init",
                    "--yes",
                    "--no-secrets",
                    "--dataset", "codemind_demo",
                    "--depth", "5",
                    "--since", "2025-01-01",
                    "--scope", "private",
                    "--cognee-url", "https://tenant.example",
                    "--cognee-api-key", "ck_test",
                    "--cognee-tenant-id", "tenant",
                    "--cognee-user-id", "user",
                    "--ollama-api-key", "oa_test",
                    "--ollama-model", "gpt-oss:120b",
                    "--ollama-base-url", "https://ollama.com/v1",
                ]
                first = runner.invoke(main, args)
                self.assertEqual(first.exit_code, 0, first.output)
                env_path = root / ".env"
                self.assertTrue(env_path.exists())
                env_text = env_path.read_text()
                self.assertIn("COGNEE_DATASET=codemind_demo", env_text)
                self.assertIn("OLLAMA_MODEL=gpt-oss:120b", env_text)
                self.assertTrue((root / ".github/workflows/codemind-pr.yml").exists())
                self.assertTrue((root / ".github/workflows/codemind-reconcile.yml").exists())
                self.assertFalse((root / ".github/workflows/codemind-ingest.yml").exists())
                self.assertTrue((root / "codemind_config.json").exists())

                second = runner.invoke(main, args)
                self.assertEqual(second.exit_code, 0, second.output)
                self.assertEqual(env_text, env_path.read_text())

    def test_memory_prune_dry_run_lists_decay_candidate(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = Path.cwd()
            self._init_git_repo(root)
            registry = {
                "D1": {
                    "data_id": "data-old-1",
                    "sha": "a" * 40,
                    "commit_date": "2024-01-01T00:00:00+00:00",
                    "decision": "Cache layer must be Redis",
                    "rationale": "avoid stale shared state",
                    "scope": "src/cache",
                    "importance": 0.9,
                    "status": "active",
                },
                "D2": {
                    "data_id": "data-old-2",
                    "sha": "b" * 40,
                    "commit_date": "2024-01-02T00:00:00+00:00",
                    "decision": "Use apiClient for requests",
                    "rationale": "central auth headers",
                    "scope": "src/api",
                    "importance": 0.8,
                    "status": "active",
                },
                "D3": {
                    "data_id": "data-new-1",
                    "sha": "c" * 40,
                    "commit_date": "2026-06-15T00:00:00+00:00",
                    "decision": "Keep logger structured",
                    "rationale": "observability",
                    "scope": "src/log",
                    "importance": 0.7,
                    "status": "active",
                },
            }
            events = [
                {"ts": 1.0, "kind": "remember", "decision_id": "D1", "data_id": "data-old-1"},
                {"ts": 2.0, "kind": "remember", "decision_id": "D2", "data_id": "data-old-2"},
                {"ts": 3.0, "kind": "remember", "decision_id": "D3", "data_id": "data-new-1"},
                {"ts": 4.0, "kind": "contradiction", "decision_id": "D2", "data_id": "data-old-2"},
            ]
            (root / "memory_registry.json").write_text(json.dumps(registry, indent=2))
            (root / "event_log.json").write_text(json.dumps(events, indent=2))
            result = runner.invoke(main, ["memory", "prune", "--older-than", "90d", "--dry-run"])
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertIn("D1", result.output)
            self.assertNotIn("D2", result.output)
            self.assertNotIn("D3", result.output)

    def test_link_uses_same_secret_injection_helper_as_init(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = Path.cwd()
            self._init_git_repo(root)
            (root / ".env").write_text(
                "\n".join([
                    "COGNEE_URL=https://tenant.example",
                    "COGNEE_API_KEY=ck_test",
                    "COGNEE_TENANT_ID=tenant",
                    "COGNEE_USER_ID=user",
                    "COGNEE_DATASET=codemind_demo",
                    "OLLAMA_API_KEY=oa_test",
                    "OLLAMA_MODEL=gpt-oss:120b",
                ])
                + "\n"
            )
            with patch("codemind.cli.validate_cognee_credentials", AsyncMock(return_value=ValidationResult(True, "ok"))), \
                 patch("codemind.cli.validate_ollama_credentials", AsyncMock(return_value=ValidationResult(True, "ok"))), \
                 patch("codemind.cli.gh_auth_status", return_value=(True, "ok")), \
                  patch("codemind.cli._gh_repo_name", return_value=root.name), \
                 patch("codemind.cli.set_repo_secrets", return_value={"ok": 5, "failed": 0, "skipped": []}) as set_repo_secrets, \
                 patch("codemind.cli.copy_workflows", return_value=[]), \
                 patch("codemind.cli.set_repo_variable", return_value=True):
                init_result = runner.invoke(
                    main,
                    [
                        "init",
                        "--yes",
                        "--dataset", "codemind_demo",
                        "--no-workflows",
                        "--cognee-url", "https://tenant.example",
                        "--cognee-api-key", "ck_test",
                        "--cognee-tenant-id", "tenant",
                        "--cognee-user-id", "user",
                        "--ollama-api-key", "oa_test",
                        "--ollama-model", "gpt-oss:120b",
                        "--ollama-base-url", "https://ollama.com/v1",
                    ],
                )
                self.assertEqual(init_result.exit_code, 0, init_result.output)
                link_result = runner.invoke(main, ["link", "--repo", "owner/repo-b", "--no-workflows"])
                self.assertEqual(link_result.exit_code, 0, link_result.output)
                self.assertGreaterEqual(set_repo_secrets.call_count, 2)
                self.assertEqual(set_repo_secrets.call_args_list[0].args[0], root.name)
                self.assertEqual(set_repo_secrets.call_args_list[1].args[0], "owner/repo-b")

    def test_hard_prune_only_uses_forget_many(self):
        runner = CliRunner()
        fake_registry = MagicMock()
        fake_registry.load_registry.return_value = {
            "D1": {
                "data_id": "data-old-1",
                "sha": "a" * 40,
                "commit_date": "2024-01-01T00:00:00+00:00",
                "decision": "Cache layer must be Redis",
                "rationale": "avoid stale shared state",
                "scope": "src/cache",
                "importance": 0.9,
                "status": "active",
            }
        }
        fake_registry.load_events.return_value = []
        fake_registry.all_active.return_value = list(fake_registry.load_registry.return_value.values())
        fake_registry.find_by_decision_text.return_value = fake_registry.load_registry.return_value["D1"]
        fake_registry.upsert_entry.return_value = None
        fake_registry.append_event.return_value = None

        fake_client = MagicMock()
        fake_client.connect = AsyncMock(return_value=None)
        fake_client.disconnect = AsyncMock(return_value=None)
        fake_client.forget_many = AsyncMock(return_value={"ok": 1, "failed": 0, "errors": []})
        fake_client.forget_dataset = MagicMock(side_effect=AssertionError("full-dataset forget must not be used"))
        fake_client.remember_decision = AsyncMock(return_value={"data_id": "new", "dataset_id": "d", "raw": "ok"})

        with patch("codemind.cli._mods", return_value={"registry": fake_registry, "cognee_client": fake_client, "ROOT": Path.cwd()}):
            result = runner.invoke(main, ["memory", "prune", "--older-than", "90d", "--hard"])
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertTrue(fake_client.forget_many.await_count >= 1)
            self.assertFalse(fake_client.forget_dataset.called)


if __name__ == "__main__":
    unittest.main(verbosity=2)
