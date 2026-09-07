from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from eval_routing import evaluate  # noqa: E402
from model_router import evaluation_identity  # noqa: E402


def digest(answer: str) -> str:
    return hashlib.sha256(answer.encode("utf-8")).hexdigest()


class EvaluationKindsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "knowledge-base"
        (self.root / "meta").mkdir(parents=True)
        (self.root / "meta/corpus-manifest.yaml").write_text(
            yaml.safe_dump({"schema_version": 2}), encoding="utf-8"
        )
        self.routed = {"cases": [{"id": "case-1", "answer": "Ответ с [doc-1]"}]}
        self.sol = {"cases": [{"id": "case-1", "answer": "Базовый ответ с [doc-1]"}]}

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_default_contract_result_is_format_only_not_factual_grounding(self) -> None:
        result = evaluate(
            {"name": "format", "cases": [{"id": "case-1", "contract": {"evidence_ids": ["doc-1"]}}]},
            self.routed,
            self.sol,
        )
        self.assertEqual("format_only", result["evaluation_kind"])
        dimensions = result["comparisons"][0]["routed_dimensions"]
        self.assertEqual(1.0, dimensions["evidence_id_presence"])
        self.assertNotIn("factual_grounding", dimensions)
        self.assertEqual("format_contract", result["comparisons"][0]["evaluation_source"])

    def test_reviewed_answers_require_hash_bound_grading_and_current_identity(self) -> None:
        dataset = {
            "name": "reviewed", "evaluation_kind": "reviewed_answers",
            "identity": evaluation_identity(self.root),
            "cases": [{"id": "case-1"}],
            "reviewer_grades": [{
                "id": "case-1",
                "routed": {"reviewer_id": "reviewer-a", "reviewed_at": "2026-09-07T10:00:00Z", "rubric_version": "retrieval-v1", "answer_sha256": digest("Ответ с [doc-1]"), "score": 0.8},
                "sol": {"reviewer_id": "reviewer-a", "reviewed_at": "2026-09-07T10:01:00Z", "rubric_version": "retrieval-v1", "answer_sha256": digest("Базовый ответ с [doc-1]"), "score": 0.9},
            }],
        }
        result = evaluate(dataset, self.routed, self.sol, root=self.root)
        self.assertEqual("reviewed_answers", result["evaluation_kind"])
        self.assertEqual(evaluation_identity(self.root), result["identity"])
        self.assertEqual("reviewer_grading", result["comparisons"][0]["evaluation_source"])
        self.assertEqual(digest("Ответ с [doc-1]"), result["comparisons"][0]["routed_review"]["answer_sha256"])
        (self.root / "meta/corpus-manifest.yaml").write_text(
            yaml.safe_dump({"schema_version": 3}), encoding="utf-8"
        )
        self.assertNotEqual(result["identity"], evaluation_identity(self.root))

    def test_stale_or_missing_reviewer_grading_is_rejected(self) -> None:
        stale = {
            "evaluation_kind": "reviewed_answers", "cases": [{"id": "case-1"}],
            "identity": evaluation_identity(self.root),
            "reviewer_grades": [{
                "id": "case-1",
                "routed": {"reviewer_id": "reviewer-a", "reviewed_at": "2026-09-07", "rubric_version": "retrieval-v1", "answer_sha256": "0" * 64, "score": 1},
                "sol": {"reviewer_id": "reviewer-a", "reviewed_at": "2026-09-07", "rubric_version": "retrieval-v1", "answer_sha256": digest("Базовый ответ с [doc-1]"), "score": 1},
            }],
        }
        with self.assertRaisesRegex(ValueError, "устарела"):
            evaluate(stale, self.routed, self.sol, root=self.root)
        missing = {
            "evaluation_kind": "reviewed_answers", "identity": evaluation_identity(self.root),
            "cases": [{"id": "case-1"}], "reviewer_grades": [],
        }
        with self.assertRaisesRegex(ValueError, "ровно по одной"):
            evaluate(missing, self.routed, self.sol, root=self.root)

    def test_reviewed_dataset_snapshot_rejects_changed_corpus_before_evaluation(self) -> None:
        dataset = {
            "evaluation_kind": "reviewed_answers", "identity": evaluation_identity(self.root),
            "cases": [{"id": "case-1"}], "reviewer_grades": [],
        }
        (self.root / "meta/corpus-manifest.yaml").write_text(
            yaml.safe_dump({"schema_version": 3}), encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "dataset.identity"):
            evaluate(dataset, self.routed, self.sol, root=self.root)

    def test_evaluation_identity_ignores_manifest_timestamp_but_detects_normalized_change(self) -> None:
        normalized = self.root / "normalized/source.txt"
        normalized.parent.mkdir(exist_ok=True)
        normalized.write_text("first source", encoding="utf-8")
        (self.root / "meta/documents.yaml").write_text(yaml.safe_dump({"documents": [{
            "id": "doc-1", "normalized_path": "normalized/source.txt", "indexed_at": "2026-09-07T10:00:00Z",
        }]}), encoding="utf-8")
        (self.root / "meta/corpus-manifest.yaml").write_text(yaml.safe_dump({
            "schema_version": 2, "last_indexed_at": "2026-09-07T10:00:00Z", "retrieval_index": {"documents": 1},
        }), encoding="utf-8")
        initial = evaluation_identity(self.root)
        (self.root / "meta/corpus-manifest.yaml").write_text(yaml.safe_dump({
            "schema_version": 2, "last_indexed_at": "2026-09-08T10:00:00Z", "retrieval_index": {"documents": 99},
        }), encoding="utf-8")
        self.assertEqual(initial, evaluation_identity(self.root))
        normalized.write_text("changed normalized source", encoding="utf-8")
        self.assertNotEqual(initial, evaluation_identity(self.root))

    def test_pending_review_dataset_cannot_be_presented_as_quality_result(self) -> None:
        with self.assertRaisesRegex(ValueError, "не является завершённой оценкой"):
            evaluate({"evaluation_kind": "pending_review", "cases": [{"id": "case-1"}]}, self.routed, self.sol)

    def test_unknown_or_invalid_token_measurements_are_not_economy_metrics(self) -> None:
        dataset = {"cases": [{"id": "case-1"}]}
        partial = evaluate(dataset, self.routed, self.sol)
        self.assertIsNone(partial["tokens"]["input"])
        self.assertIsNone(partial["tokens"]["total"])
        self.assertEqual("partial", partial["tokens"]["source"])
        self.assertIsNone(partial["tokens"]["total_delta_routed_minus_sol"])
        bad_routed = {"cases": [{"id": "case-1", "answer": "Ответ", "input_tokens": -1}]}
        with self.assertRaisesRegex(ValueError, "неотрицательным"):
            evaluate(dataset, bad_routed, self.sol)


if __name__ == "__main__":
    unittest.main()
