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
KB_SCRIPT = PLUGIN_ROOT / "scripts" / "kb.py"


class RetrievalCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        for directory in ("docs/design/gost", "raw", "normalized", "meta", "staging/stage-1", "reports"):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        self._write_yaml("meta/corpus-manifest.yaml", {"schema_version": 2, "last_indexed_at": None})
        self._write_yaml("meta/documents.yaml", {"documents": []})
        self._write_yaml("meta/categories.yaml", {
            "categories": [{"id": "design", "title": "Проектирование", "description": "Проектные нормы"}]
        })
        self._write_yaml("meta/cross-references.yaml", {"sentinel": "FULL_REGISTRY_MUST_NOT_LEAK"})
        self._write_yaml("meta/addition-queue.yaml", {"sentinel": "QUEUE_MUST_NOT_LEAK", "queue": []})
        self._write_yaml("meta/replacements.yaml", {"replacements": []})
        normalized = (
            "===== СТРАНИЦА 1 =====\n"
            "1 Общие положения\n"
            "Вводный текст стандарта.\n"
            "1.1 Требования к изделию\n"
            + "Требование к качеству и контролю. " * 40
            + "\n===== СТРАНИЦА 2 =====\n"
            "2 Следующий раздел\n"
            "Этот текст не относится к пункту 1.1.\n"
        )
        (self.root / "normalized/gost-1-2-2024.txt").write_text(normalized, encoding="utf-8")
        card = {
            "id": "gost-1-2-2024",
            "type": "ГОСТ",
            "title": "Испытательный стандарт",
            "short_title": "ГОСТ 1.2-2024",
            "issuer": "Испытательный орган",
            "status": "действует",
            "category": ["design"],
            "applies_to": ["контроль качества изделий"],
            "source": {
                "original_file": "raw/design/gost/gost-1-2-2024.pdf",
                "normalized_file": "normalized/gost-1-2-2024.txt",
                "sha256": "abc123",
            },
            "verification": {"legal_status": "требует_проверки"},
            "lifecycle": {"stage": "requires_expert_review"},
            "relations": {"replaces": [], "replaced_by": [], "related_documents": []},
            "references": [],
        }
        yaml_text = yaml.safe_dump(card, allow_unicode=True, sort_keys=False)
        (self.root / "docs/design/gost/gost-1-2-2024.md").write_text(
            f"---\n{yaml_text}---\n# ГОСТ 1.2-2024\n", encoding="utf-8"
        )
        self._write_yaml("staging/stage-1/manifest.yaml", {
            "stage_id": "stage-1",
            "state": "waiting_for_ai_analysis",
            "source_name": "ГОСТ 1.2-2024.pdf",
            "source_sha256": "new-hash",
            "extracted_text": "staging/stage-1/extracted.txt",
            "characters_extracted": 1000,
        })
        (self.root / "staging/stage-1/extracted.txt").write_text("ГОСТ 1.2-2024", encoding="utf-8")
        self.run_cli("rebuild-index", json_output=False)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_yaml(self, relative_path: str, data: dict) -> None:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    def run_cli(self, *args: str, json_output: bool = True):
        environment = os.environ.copy()
        environment["NV2_NUCLEAR_KB_ROOT"] = str(self.root)
        environment["PYTHONUTF8"] = "1"
        completed = subprocess.run(
            [sys.executable, str(KB_SCRIPT), *args],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        if completed.returncode:
            self.fail(f"kb.py {' '.join(args)} failed:\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")
        return json.loads(completed.stdout) if json_output else completed.stdout

    def test_rebuild_creates_structural_and_search_indexes(self) -> None:
        clause_index = json.loads((self.root / "meta/clause-index.json").read_text(encoding="utf-8"))
        search_index = json.loads((self.root / "meta/search-index.json").read_text(encoding="utf-8"))
        indexed = clause_index["documents"]["gost-1-2-2024"]
        self.assertEqual([1, 2], [item["page"] for item in indexed["pages"]])
        self.assertIn("1.1", [item["clause"] for item in indexed["clauses"]])
        self.assertEqual("gost-1-2-2024", search_index["documents"][0]["id"])

    def test_search_prefers_exact_designation(self) -> None:
        result = self.run_cli("search", "ГОСТ 1.2-2024", "--limit", "3", "--max-chars", "3000")
        self.assertEqual("gost-1-2-2024", result["results"][0]["id"])
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False, separators=(",", ":"))), 3000)

    def test_search_uses_normalized_content_without_returning_it(self) -> None:
        result = self.run_cli("search", "вводный", "--limit", "3", "--max-chars", "3000")
        self.assertEqual("gost-1-2-2024", result["results"][0]["id"])
        self.assertIn("content", result["results"][0]["matched_fields"])
        self.assertNotIn("Вводный текст стандарта", json.dumps(result, ensure_ascii=False))

    def test_fetch_clause_stops_before_next_peer_clause(self) -> None:
        result = self.run_cli("fetch", "gost-1-2-2024", "--clauses", "1.1", "--max-chars", "4000")
        text = result["excerpts"][0]["text"]
        self.assertIn("Требование к качеству", text)
        self.assertNotIn("2 Следующий раздел", text)
        self.assertEqual(1, result["excerpts"][0]["page"])

    def test_fetch_enforces_excerpt_budget_and_marks_truncation(self) -> None:
        result = self.run_cli("fetch", "gost-1-2-2024", "--clauses", "1", "--max-chars", "500")
        self.assertTrue(result["truncated"])
        self.assertFalse(result["complete"])
        self.assertLessEqual(len(result["excerpts"][0]["text"]), 500)

    def test_archive_context_does_not_emit_full_registries(self) -> None:
        result = self.run_cli(
            "archive-context", "stage-1", "--reference", "ГОСТ 1.2-2024", "--max-chars", "5000"
        )
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("FULL_REGISTRY_MUST_NOT_LEAK", serialized)
        self.assertNotIn("QUEUE_MUST_NOT_LEAK", serialized)
        self.assertEqual("gost-1-2-2024", result["reference_resolutions"][0]["target_document"])


if __name__ == "__main__":
    unittest.main()
