from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
from evidence_validation import answer_digest, evidence_digest, validate_evidence  # noqa: E402


class EvidenceValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        for directory in ("meta", "normalized"):
            (self.root / directory).mkdir(parents=True)
        text = (
            "===== СТРАНИЦА 1 =====\n"
            "1 Область применения\n"
            "Цитата находится только в пункте один.\n"
            "===== СТРАНИЦА 2 =====\n"
            "2 Другое\n"
            "Цитата находится только на странице два.\n"
        )
        normalized = self.root / "normalized/doc-1.txt"
        normalized.write_text(text, encoding="utf-8")
        self.normalized_hash = hashlib.sha256(normalized.read_bytes()).hexdigest()
        (self.root / "meta/documents.yaml").write_text(yaml.safe_dump({"documents": [{
            "id": "doc-1", "normalized_path": "normalized/doc-1.txt", "source_sha256": "original-hash",
        }]}, allow_unicode=True), encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_metadata_mode_accepts_registry_backed_id_without_a_passage(self) -> None:
        result = validate_evidence(self.root, "Вывод по doc-1.", {
            "evidence_ids": ["doc-1"], "evidence_mode": "metadata",
        })
        self.assertTrue(result["passed"], result)

    def test_invented_document_id_fails_closed(self) -> None:
        result = validate_evidence(self.root, "Вывод по invented.", {
            "evidence_ids": ["invented"], "evidence_mode": "metadata",
        })
        self.assertFalse(result["passed"])
        self.assertIn("unknown_document:invented", result["failures"])

    def test_clause_and_page_are_an_intersection_not_a_union(self) -> None:
        result = validate_evidence(self.root, "Вывод doc-1.", {
            "evidence_mode": "passage",
            "evidence": [{"document_id": "doc-1", "clause": "1", "page": 2,
                          "quote": "Цитата находится только в пункте один."}],
        })
        self.assertFalse(result["passed"])
        self.assertIn("locator_intersection_empty:doc-1", result["failures"])

    def test_anchored_clause_locator_is_accepted_only_at_its_exact_line(self) -> None:
        result = validate_evidence(self.root, "Вывод doc-1.", {
            "evidence_mode": "passage",
            "evidence": [{"document_id": "doc-1", "clause": "1@line:2", "page": 1,
                          "quote": "Цитата находится только в пункте один."}],
        })
        self.assertTrue(result["passed"], result)

    def test_changed_normalized_hash_and_stale_semantic_review_fail(self) -> None:
        answer = "Вывод doc-1. Цитата находится только в пункте один."
        evidence = [{"document_id": "doc-1", "clause": "1", "page": 1,
                     "quote": "Цитата находится только в пункте один.", "source_sha256": "not-current"}]
        result = validate_evidence(self.root, answer, {
            "evidence_mode": "passage", "evidence": evidence,
            "semantic_review": {"reviewer": "expert", "supported": True,
                                "answer_sha256": answer_digest("other"), "evidence_sha256": evidence_digest(evidence)},
        })
        self.assertFalse(result["passed"])
        self.assertIn("source_changed:doc-1", result["failures"])
        self.assertIn("semantic_review_stale", result["failures"])

    def test_high_criticality_requires_a_source_version_pin(self) -> None:
        result = validate_evidence(self.root, "Вывод doc-1.", {
            "evidence_mode": "passage", "require_semantic_review": True,
            "evidence": [{"document_id": "doc-1", "clause": "1", "quote": "Цитата находится только в пункте один."}],
        })
        self.assertIn("source_version_required:doc-1", result["failures"])
        self.assertIn("semantic_review_required", result["failures"])


if __name__ == "__main__":
    unittest.main()
