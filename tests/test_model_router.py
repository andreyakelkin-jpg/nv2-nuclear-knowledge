from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "scripts"
KB_SCRIPT = SCRIPTS / "kb.py"
sys.path.insert(0, str(SCRIPTS))

from model_router import assess_route, non_inferiority_gate, validate_answer_text  # noqa: E402


class DeterministicRoutingTests(unittest.TestCase):
    def test_representative_cases_select_expected_tier(self) -> None:
        datasets = ("routing-cases.yaml", "routing-regression-simple-questions.yaml")
        for dataset_name in datasets:
            dataset = yaml.safe_load((PLUGIN_ROOT / f"evals/{dataset_name}").read_text(encoding="utf-8"))
            for case in dataset["cases"]:
                with self.subTest(dataset=dataset_name, case=case["id"]):
                    route = assess_route(**case["assessment"])
                    self.assertEqual(case["expected_tier"], route["tier"])

    def test_bounded_retrieval_volume_does_not_force_sol(self) -> None:
        two_calls = assess_route(
            complexity="low", ambiguity="low", criticality="low", context_chars=8000,
            tool_count=2, side_effects="none", confidence=0.94,
        )
        four_calls = assess_route(
            complexity="low", ambiguity="medium", criticality="low", context_chars=8000,
            tool_count=4, side_effects="none", confidence=0.92,
        )
        self.assertEqual("luna", two_calls["tier"])
        self.assertEqual("terra", four_calls["tier"])

    def test_borderline_is_bumped_to_sol(self) -> None:
        route = assess_route(
            complexity="medium",
            ambiguity="medium",
            criticality="medium",
            context_chars=70000,
            tool_count=2,
            side_effects="none",
            confidence=0.82,
        )
        self.assertEqual("sol", route["tier"])
        self.assertIn("borderline_bumped_to_stronger_model", route["reason"])

    def test_answer_contract_checks_all_required_dimensions(self) -> None:
        result = validate_answer_text("Краткий ответ без доказательства", {
            "min_chars": 100,
            "required_strings": ["вывод"],
            "required_sections": ["Источники"],
            "evidence_ids": ["np-104-18"],
            "format": "json",
        })
        self.assertFalse(result["passed"])
        self.assertTrue(all(not value for value in result["checks"].values()))

    def test_non_inferiority_gate_passes_equal_quality_and_rejects_degradation(self) -> None:
        equal = [{"routed_score": 1.0, "sol_score": 1.0} for _ in range(15)]
        degraded = [{"routed_score": 0.75, "sol_score": 1.0} for _ in range(15)]
        self.assertEqual("passed", non_inferiority_gate(equal, margin=0.05, minimum_cases=12)["status"])
        self.assertEqual("failed", non_inferiority_gate(degraded, margin=0.05, minimum_cases=12)["status"])


class RoutingCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        for directory in ("docs", "raw", "normalized", "meta", "reports/model-routing"):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        self._write_yaml(self.root / "meta/documents.yaml", {"documents": []})
        self._write_yaml(self.root / "meta/corpus-manifest.yaml", {"schema_version": 2})
        self._write_yaml(self.root / "reports/model-routing/gate-latest.yaml", {"status": "passed"})
        self.config = self.root / "config.yaml"
        self._write_yaml(self.config, {
            "kb_root": str(self.root),
            "routing": {"enabled": True, "require_quality_gate": True, "minimum_eval_cases": 12},
        })
        self.environment = os.environ.copy()
        self.environment.update({
            "NV2_NUCLEAR_KB_ROOT": str(self.root),
            "NV2_NUCLEAR_CONFIG_PATH": str(self.config),
            "PYTHONUTF8": "1",
        })

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _write_yaml(path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    def cli(self, *args: str) -> dict:
        completed = subprocess.run(
            [sys.executable, str(KB_SCRIPT), *args],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=self.environment,
        )
        if completed.returncode:
            self.fail(f"kb.py {' '.join(args)} failed:\n{completed.stdout}\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_failed_luna_answer_escalates_once_to_terra(self) -> None:
        route = self.cli(
            "route", "--task-id", "test-lookup", "--complexity", "low", "--ambiguity", "low",
            "--criticality", "low", "--context-chars", "1000", "--tool-count", "1",
            "--side-effects", "none", "--confidence", "0.96",
        )
        self.assertEqual("gpt-5.6-luna", route["model"])
        answer = self.root / "answer.txt"
        answer.write_text("неполный", encoding="utf-8")
        contract = self.root / "contract.yaml"
        self._write_yaml(contract, {"min_chars": 50, "required_strings": ["источник"]})
        checked = self.cli("route-check", route["run_id"], "--answer", str(answer), "--contract", str(contract))
        self.assertEqual("gpt-5.6-terra", checked["escalate_once"]["model"])
        escalated = self.cli("route-escalate", route["run_id"])
        self.assertEqual("gpt-5.6-terra", escalated["model"])
        repeated = subprocess.run(
            [sys.executable, str(KB_SCRIPT), "route-escalate", route["run_id"]],
            check=False, capture_output=True, text=True, encoding="utf-8", env=self.environment,
        )
        self.assertNotEqual(0, repeated.returncode)
        self.assertIn("уже использована", repeated.stderr)
        second_check = self.cli(
            "route-check", escalated["run_id"], "--answer", str(answer), "--contract", str(contract)
        )
        self.assertIsNone(second_check["escalate_once"])
        events = [json.loads(line) for line in (self.root / "reports/model-routing/events.jsonl").read_text(encoding="utf-8").splitlines()]
        final = events[-1]
        for field in ("model", "effort", "reason", "confidence", "escalation", "tokens", "latency_ms"):
            self.assertIn(field, final)

    def test_side_effect_claim_is_allowed_once_after_acceptance(self) -> None:
        route = self.cli(
            "route", "--task-id", "local-write", "--complexity", "medium", "--ambiguity", "low",
            "--criticality", "medium", "--context-chars", "5000", "--tool-count", "2",
            "--side-effects", "local", "--confidence", "0.91",
        )
        answer = self.root / "accepted.txt"
        answer.write_text("Проверенный ответ с источником np-104-18.", encoding="utf-8")
        contract = self.root / "accepted-contract.yaml"
        self._write_yaml(contract, {"min_chars": 20, "evidence_ids": ["np-104-18"]})
        checked = self.cli("route-check", route["run_id"], "--answer", str(answer), "--contract", str(contract))
        self.assertTrue(checked["accepted"])
        claimed = self.cli("route-claim", route["run_id"], "--operation-id", "write-card-1")
        self.assertTrue(claimed["execute_once"])
        repeated = subprocess.run(
            [sys.executable, str(KB_SCRIPT), "route-claim", route["run_id"], "--operation-id", "write-card-1"],
            check=False, capture_output=True, text=True, encoding="utf-8", env=self.environment,
        )
        self.assertNotEqual(0, repeated.returncode)
        self.assertIn("повтор запрещён", repeated.stderr)

    def test_disabled_flag_fails_closed_to_sol(self) -> None:
        self._write_yaml(self.config, {"kb_root": str(self.root), "routing": {"enabled": False}})
        route = self.cli(
            "route", "--task-id", "simple", "--complexity", "low", "--ambiguity", "low",
            "--criticality", "low", "--context-chars", "100", "--tool-count", "0",
            "--side-effects", "none", "--confidence", "0.99",
        )
        self.assertEqual("gpt-5.6-sol", route["model"])
        self.assertFalse(route["routing_enabled"])

    def test_failed_quality_gate_auto_disables_routing(self) -> None:
        comparison = self.root / "comparison.yaml"
        self._write_yaml(comparison, {
            "dataset": "degraded-test",
            "comparisons": [
                {"id": f"case-{index}", "routed_score": 0.7, "sol_score": 1.0}
                for index in range(12)
            ],
        })
        result = self.cli("routing-gate", str(comparison))
        self.assertEqual("failed", result["gate"]["status"])
        self.assertFalse(result["routing"]["configured_enabled"])
        self.assertFalse(result["routing"]["effective_enabled"])


if __name__ == "__main__":
    unittest.main()
