from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import yaml


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
from assess_training import AssessmentValidationError, evaluate_assessment, validate_assignment  # noqa: E402
from training_impact import training_impact  # noqa: E402


class TrainingAssessmentQualityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        for directory in ("training/question-bank", "training/cases", "meta", "normalized"):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        self.documents = [{"id": "doc-1", "revision_date": "2026-01", "source_sha256": "original-current",
                           "normalized_sha256": "normalized-current", "lifecycle_stage": "expert_reviewed"}]

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, relative: str, value: dict) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")

    def assignment(self, items: list[dict], **extra: object) -> dict:
        return {"id": "assessment-1", "pass_score": 80, "critical_errors_allowed": 0, "items": items, **extra}

    def question(self, item_id: str, question_type: str, correct: object = "a", **extra: object) -> None:
        value = {"id": item_id, "type": question_type,
                 "source": {"document_id": "doc-1", "sha256": "original-current"}, **extra}
        if correct is not None:
            value["correct_answer"] = correct
        self.write(f"training/question-bank/{item_id}.yaml", value)

    def test_critical_case_cannot_pass_despite_perfect_score(self) -> None:
        self.write("training/cases/case-1.yaml", {
            "id": "case-1", "source": {"document_id": "doc-1", "sha256": "original-current"},
        })
        report = evaluate_assessment(self.assignment([{"kind": "case", "id": "case-1", "weight": 1}]),
            {"answers": {"case-1": {"expert_score": 100, "evaluator": "expert", "critical_failure": True}}}, self.root, self.documents)
        self.assertEqual(100.0, report["score"])
        self.assertEqual("failed", report["result"])
        self.assertEqual(["case-1"], report["fatal_case_items"])

    def test_multiple_choice_uses_sets_and_wrong_choice_fails(self) -> None:
        self.question("multi", "multiple_choice", ["a", "b"])
        assignment = self.assignment([{"kind": "question", "id": "multi", "weight": 1}])
        reordered = evaluate_assessment(assignment, {"answers": {"multi": {"answer": ["b", "a"]}}}, self.root, self.documents)
        wrong = evaluate_assessment(assignment, {"answers": {"multi": {"answer": ["a", "c"]}}}, self.root, self.documents)
        self.assertEqual("passed", reordered["result"])
        self.assertEqual("failed", wrong["result"])

    def test_missing_correct_answer_and_empty_answer_never_passes(self) -> None:
        self.question("missing", "single_choice", None)
        report = evaluate_assessment(self.assignment([{"kind": "question", "id": "missing", "weight": 1}]),
            {"answers": {"missing": {}}}, self.root, self.documents)
        self.assertNotEqual("passed", report["result"])
        self.assertIn("requires_review_missing_correct_answer", {entry["reason"] for entry in report["requires_review"]})

    def test_stale_declared_source_is_flagged_for_review(self) -> None:
        self.question("stale", "single_choice", "a", source={"document_id": "doc-1", "sha256": "old"})
        report = evaluate_assessment(self.assignment([{"kind": "question", "id": "stale", "weight": 1}]),
            {"answers": {"stale": {"answer": "a"}}}, self.root, self.documents)
        self.assertEqual("requires_review", report["result"])
        self.assertIn("sha256_changed", report["source_versions"][0]["changes"])

    def test_pilot_is_explicitly_reviewable_not_a_formal_pass(self) -> None:
        self.question("pilot", "single_choice", "a")
        unreviewed = [{**self.documents[0], "lifecycle_stage": "requires_expert_review"}]
        report = evaluate_assessment(self.assignment([{"kind": "question", "id": "pilot", "weight": 1}], mode="pilot"),
            {"answers": {"pilot": {"answer": "a"}}}, self.root, unreviewed)
        self.assertEqual("requires_review", report["result"])
        self.assertFalse(report["passed"])

    def test_formal_source_without_a_pinned_hash_requires_review(self) -> None:
        self.question("unpinned", "single_choice", "a", source={"document_id": "doc-1"})
        report = evaluate_assessment(self.assignment([{"kind": "question", "id": "unpinned", "weight": 1}]),
            {"answers": {"unpinned": {"answer": "a"}}}, self.root, self.documents)
        self.assertEqual("requires_review", report["result"])
        self.assertIn("source_version_unpinned", {entry["reason"] for entry in report["requires_review"]})

    def test_declared_hash_without_current_hash_requires_review(self) -> None:
        self.question("missing-current", "single_choice", "a", source={
            "document_id": "doc-1", "normalized_sha256": "expected-normalized",
        })
        current_without_normalized = [{key: value for key, value in self.documents[0].items() if key != "normalized_sha256"}]
        report = evaluate_assessment(self.assignment([{"kind": "question", "id": "missing-current", "weight": 1}]),
            {"answers": {"missing-current": {"answer": "a"}}}, self.root, current_without_normalized)
        self.assertEqual("requires_review", report["result"])
        self.assertIn("normalized_sha256_current_missing", report["source_versions"][0]["changes"])

    def test_critical_question_and_boolean_integer_choice_never_pass(self) -> None:
        self.question("critical", "single_choice", 1, critical=True)
        report = evaluate_assessment(self.assignment([{"kind": "question", "id": "critical", "weight": 1}]),
            {"answers": {"critical": {"answer": True}}}, self.root, self.documents)
        self.assertEqual("failed", report["result"])
        self.assertEqual(["critical"], report["critical_error_items"])

    def test_single_choice_rejects_collection_key_or_answer(self) -> None:
        self.question("bad-key", "single_choice", ["a"])
        self.question("bad-answer", "single_choice", "a")
        bad_key = evaluate_assessment(self.assignment([{"kind": "question", "id": "bad-key", "weight": 1}]),
            {"answers": {"bad-key": {"answer": "a"}}}, self.root, self.documents)
        bad_answer = evaluate_assessment(self.assignment([{"kind": "question", "id": "bad-answer", "weight": 1}]),
            {"answers": {"bad-answer": {"answer": ["a"]}}}, self.root, self.documents)
        self.assertEqual("requires_review", bad_key["result"])
        self.assertEqual("failed", bad_answer["result"])

    def test_empty_duplicate_and_invalid_weight_are_rejected(self) -> None:
        with self.assertRaisesRegex(AssessmentValidationError, "непустым"):
            validate_assignment({"id": "a", "items": []})
        with self.assertRaisesRegex(AssessmentValidationError, "Повтор"):
            validate_assignment(self.assignment([{"kind": "question", "id": "q", "weight": 1}, {"kind": "question", "id": "q", "weight": 1}]))
        with self.assertRaisesRegex(AssessmentValidationError, "конечным"):
            validate_assignment(self.assignment([{"kind": "question", "id": "q", "weight": float("nan")}]))
        with self.assertRaisesRegex(AssessmentValidationError, "устарел"):
            validate_assignment(self.assignment([{"kind": "question", "id": "q", "weight": 1}], critical_errors_allowed=1))

    def test_impact_reports_direct_question_and_case_dependencies(self) -> None:
        self.question("impact-q", "single_choice", "a", source={"document_id": "doc-1", "sha256": "declared"})
        self.write("training/cases/impact-case.yaml", {"id": "impact-case", "source": [{"document_id": "doc-1", "normalized_sha256": "declared-normalized"}]})
        impact = training_impact(self.root, self.documents)
        self.assertEqual(["impact-q"], impact["affected_question_ids"])
        self.assertEqual(["impact-case"], impact["affected_case_ids"])
        self.assertEqual("declared", impact["impacts"][0]["affected"][1]["source"]["sha256"])


if __name__ == "__main__":
    unittest.main()
