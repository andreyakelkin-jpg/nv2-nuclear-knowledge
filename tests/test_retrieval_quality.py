from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from reference_resolver import _document_maps, canonical_identifier, identifier_from_id, resolve_label, synchronize_references  # noqa: E402
from retrieval import _reference_resolution, build_retrieval_indexes, current_structure, fetch_document, index_normalized_document, search_documents  # noqa: E402


class RetrievalQualityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "normalized").mkdir(parents=True)
        (self.root / "meta").mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _registry(self, documents: list[dict]) -> None:
        (self.root / "meta" / "documents.yaml").write_text(
            yaml.safe_dump({"documents": documents}, allow_unicode=True), encoding="utf-8"
        )
        (self.root / "meta" / "corpus-manifest.yaml").write_text("{}\n", encoding="utf-8")
        (self.root / "meta" / "replacements.yaml").write_text("replacements: []\n", encoding="utf-8")

    def test_exact_search_never_loads_the_full_content_index(self) -> None:
        self._registry([{"id": "np-001-15", "short_title": "НП-001-15", "title": "Правила"}])
        with patch("retrieval._search_entries", side_effect=AssertionError("Full content index should not be read")):
            result = search_documents(self.root, "НП-001-15")
        self.assertEqual("exact_metadata", result["strategy"])
        self.assertEqual("np-001-15", result["results"][0]["id"])

    def test_dotted_gost_id_and_explicit_missing_edition_are_not_substituted(self) -> None:
        self.assertEqual(
            ("gost-r:50.05.02:2022", "gost-r:50.05.02"),
            identifier_from_id("gost-r-50.05.02-2022"),
        )
        exact, family = canonical_identifier("ГОСТ Р 50.05.02-2099")
        self.assertEqual("gost-r:50.05.02:2099", exact)
        target, method, key = resolve_label(
            "ГОСТ Р 50.05.02-2099",
            {"gost-r:50.05.02:2022": "gost-r-50.05.02-2022"},
            {"gost-r:50.05.02": ["gost-r-50.05.02-2022"]},
        )
        self.assertIsNone(target)
        self.assertEqual("missing_explicit_edition", method)
        self.assertEqual(exact, key)
        self.assertEqual("gost-r:50.05.02", family)

    def test_reference_resolution_lists_other_editions_without_resolving_them(self) -> None:
        document = {
            "id": "gost-r-50.05.02-2022", "short_title": "ГОСТ Р 50.05.02-2022",
            "canonical_exact": "gost-r:50.05.02:2022", "canonical_family": "gost-r:50.05.02",
        }
        self._registry([document])
        result = _reference_resolution(self.root, "ГОСТ Р 50.05.02-2099")
        self.assertEqual("missing_explicit_edition", result["status"])
        self.assertIsNone(result["target_document"])
        self.assertEqual(["gost-r-50.05.02-2022"], result["available_editions"])
        fetched = fetch_document(self.root, "ГОСТ Р 50.05.02-2099", None, None, None)
        self.assertEqual("document_not_found_or_ambiguous", fetched["error"])
        self.assertEqual(["gost-r-50.05.02-2022"], fetched["candidates"])

    def test_sync_removes_a_wrong_in_base_edition_without_overwriting_expert_basis(self) -> None:
        docs = self.root / "docs"
        docs.mkdir()
        source = {
            "id": "source", "short_title": "Источник", "references": [{
                "cited_as": "ГОСТ Р 50.05.02-2099", "status": "в_базе",
                "target_document": "gost-r-50.05.02-2022", "basis": "Проверено экспертом.",
                "confidence": "high",
            }],
        }
        target = {"id": "gost-r-50.05.02-2022", "short_title": "ГОСТ Р 50.05.02-2022"}
        for name, metadata in (("source", source), ("gost-r-50.05.02-2022", target)):
            (docs / f"{name}.md").write_text(
                "---\n" + yaml.safe_dump(metadata, allow_unicode=True) + "---\n# card\n", encoding="utf-8"
            )
        outcome = synchronize_references(self.root)
        graph = yaml.safe_load((self.root / "relations" / "references" / "source.yaml").read_text(encoding="utf-8"))
        reference = graph["references"][0]
        self.assertEqual(1, outcome["repaired_references"])
        self.assertEqual("отсутствует", reference["status"])
        self.assertNotIn("target_document", reference)
        self.assertEqual("Проверено экспертом.", reference["basis"])

    def test_unqualified_or_duplicate_exact_designations_never_resolve_by_scan_order(self) -> None:
        docs = self.root / "docs"
        docs.mkdir()
        for name, title in (
            ("gost-r-50.05.02-2022", "ГОСТ Р 50.05.02-2022"),
            ("gost-r-50.05.02-2023", "ГОСТ Р 50.05.02-2023"),
            ("gost-r-50.05.03-2022-a", "ГОСТ Р 50.05.03-2022"),
            ("gost-r-50.05.03-2022-b", "ГОСТ Р 50.05.03-2022"),
        ):
            (docs / f"{name}.md").write_text(
                "---\n" + yaml.safe_dump({"id": name, "short_title": title}, allow_unicode=True) + "---\n# card\n",
                encoding="utf-8",
            )
        exact, families, _ = _document_maps(self.root)
        target, method, _ = resolve_label("ГОСТ Р 50.05.02", exact, families)
        self.assertIsNone(target)
        self.assertEqual("unresolved", method)
        target, method, _ = resolve_label("ГОСТ Р 50.05.03-2022", exact, families)
        self.assertIsNone(target)
        self.assertEqual("ambiguous_exact", method)

    def test_unpaged_dates_and_repeated_clause_selectors_are_safe(self) -> None:
        path = self.root / "normalized" / "a.txt"
        path.write_text(
            "31.07.2026 Система ГАРАНТ\n1 Требования\nПервый текст.\n"
            "Приложение А\n1 Требования приложения\nВторой текст.\n", encoding="utf-8"
        )
        structural = index_normalized_document(self.root, path)
        self.assertEqual([], structural["pages"])
        self.assertEqual("page_markers_absent", structural["source_quality"])
        self.assertEqual(["1", "1"], [item["clause"] for item in structural["clauses"]])
        document = {
            "id": "a", "short_title": "ГОСТ 1-2024", "title": "A", "normalized_path": "normalized/a.txt",
            "canonical_exact": "gost:1:2024", "canonical_family": "gost:1",
        }
        self._registry([document])
        build_retrieval_indexes(self.root, [document], "now")
        result = fetch_document(self.root, "a", ["1"], None, None)
        self.assertFalse(result["complete"])
        self.assertEqual([], result["excerpts"])
        self.assertEqual("1", result["ambiguous"]["clauses"][0]["clause"])
        anchor = result["ambiguous"]["clauses"][0]["anchors"][1]
        refined = fetch_document(self.root, "a", [f"1@{anchor}"], None, None)
        self.assertTrue(refined["complete"])
        self.assertIn("Второй текст", refined["excerpts"][0]["text"])
        wrong_anchor = fetch_document(self.root, "a", ["1@line:999"], None, None)
        self.assertFalse(wrong_anchor["complete"])
        self.assertEqual(["1@line:999"], wrong_anchor["not_found"]["clauses"])

    def test_query_ranks_later_full_match_and_marks_empty_query_incomplete(self) -> None:
        path = self.root / "normalized" / "b.txt"
        path.write_text("альфа\nшум\nальфа бета гамма\n", encoding="utf-8")
        document = {
            "id": "b", "short_title": "ГОСТ 2-2024", "title": "B", "normalized_path": "normalized/b.txt",
            "canonical_exact": "gost:2:2024", "canonical_family": "gost:2",
        }
        self._registry([document])
        build_retrieval_indexes(self.root, [document], "now")
        result = fetch_document(self.root, "b", None, None, "альфа бета гамма", context_lines=0)
        self.assertIn("альфа бета гамма", result["excerpts"][0]["text"])
        empty = fetch_document(self.root, "b", None, None, "несуществующее")
        self.assertFalse(empty["complete"])
        self.assertEqual("несуществующее", empty["not_found"]["query"])

    def test_query_budget_keeps_later_best_match_ahead_of_early_partial_hits(self) -> None:
        path = self.root / "normalized" / "ranked.txt"
        early = "\n".join(f"альфа ранний-{number}" for number in range(10))
        best = "альфа бета гамма " + ("содержимое " * 100)
        path.write_text(early + "\n" + best + "\n", encoding="utf-8")
        document = {
            "id": "ranked", "short_title": "ГОСТ 3-2024", "title": "Ranked",
            "normalized_path": "normalized/ranked.txt", "canonical_exact": "gost:3:2024",
            "canonical_family": "gost:3",
        }
        self._registry([document])
        build_retrieval_indexes(self.root, [document], "now")
        result = fetch_document(self.root, "ranked", None, None, "альфа бета гамма", context_lines=0, max_chars=500)
        self.assertTrue(result["truncated"])
        self.assertIn("альфа бета гамма", result["excerpts"][0]["text"])
        self.assertNotIn("ранний-0", result["excerpts"][0]["text"])

    def test_incremental_rebuild_reindexes_only_changed_file_and_reuses_content_terms(self) -> None:
        first = self.root / "normalized" / "first.txt"
        second = self.root / "normalized" / "second.txt"
        first.write_text("1 Первый\nисходный текст\n", encoding="utf-8")
        second.write_text("1 Второй\nнеизменяемый текст\n", encoding="utf-8")
        documents = [
            {"id": "first", "normalized_path": "normalized/first.txt", "short_title": "Первый"},
            {"id": "second", "normalized_path": "normalized/second.txt", "short_title": "Второй"},
        ]
        first_summary = build_retrieval_indexes(self.root, documents, "first")
        self.assertEqual(2, first_summary["rebuilt_documents"])
        first.write_text("1 Первый\nизменённый текст\n", encoding="utf-8")
        with patch("retrieval.index_normalized_document", wraps=index_normalized_document) as indexed:
            summary = build_retrieval_indexes(self.root, documents, "second")
        self.assertEqual(1, indexed.call_count)
        self.assertEqual(1, summary["rebuilt_documents"])
        self.assertEqual(1, summary["reused_documents"])
        with patch("retrieval.index_normalized_document", wraps=index_normalized_document) as indexed:
            structural = current_structure(self.root, second)
        self.assertEqual(0, indexed.call_count)
        self.assertEqual("normalized/second.txt", structural["path"])


if __name__ == "__main__":
    unittest.main()
