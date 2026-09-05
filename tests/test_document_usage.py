from __future__ import annotations

import csv
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


class DocumentUsageCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        temporary_root = Path(self.temporary.name)
        self.root = temporary_root / "knowledge-base"
        self.state = temporary_root / "runtime-state"
        for directory in ("docs", "raw", "normalized", "meta", "reports"):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        self.state.mkdir(parents=True)
        self._write_yaml(
            self.root / "meta/corpus-manifest.yaml", {"schema_version": 2}
        )
        self._write_yaml(
            self.root / "meta/documents.yaml",
            {
                "documents": [
                    {
                        "id": "np-001-15",
                        "short_title": "НП-001-15",
                        "title": "Общие положения обеспечения безопасности атомных станций",
                        "canonical_exact": "np:001-15",
                        "canonical_family": "np:001-15",
                    }
                ]
            },
        )
        self.environment = os.environ.copy()
        self.environment.update(
            {
                "NV2_NUCLEAR_KB_ROOT": str(self.root),
                "NV2_NUCLEAR_STATE_ROOT": str(self.state),
                "PYTHONUTF8": "1",
            }
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _write_yaml(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )

    def run_cli(self, *args: str) -> dict:
        completed = subprocess.run(
            [sys.executable, str(KB_SCRIPT), *args],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=self.environment,
        )
        if completed.returncode:
            self.fail(
                f"kb.py {' '.join(args)} failed:\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
            )
        return json.loads(completed.stdout)

    def record(
        self,
        answer_id: str,
        *,
        used: list[str] | None = None,
        missing: list[str] | None = None,
    ) -> dict:
        arguments = ["usage-record", "--answer-id", answer_id]
        for document in used or []:
            arguments.extend(["--used", document])
        for document in missing or []:
            arguments.extend(["--missing", document])
        return self.run_cli(*arguments)

    def test_records_used_and_potential_document_once_per_answer(self) -> None:
        result = self.record(
            "answer-1",
            used=["НП-001-15"],
            missing=["ГОСТ 9.999-2024"],
        )
        self.assertEqual(2, result["counter_increments"])
        usage = self.run_cli("document-usage", "--limit", "10")
        by_document = {item["document"]: item for item in usage["results"]}
        self.assertEqual(1, by_document["НП-001-15"]["used_count"])
        self.assertEqual(1, by_document["ГОСТ 9.999-2024"]["potential_count"])
        demand = self.run_cli("demand-priorities", "--limit", "10")
        self.assertEqual("ГОСТ 9.999-2024", demand["results"][0]["document"])
        self.assertTrue((self.state / "meta/document-usage.csv").is_file())

    def test_technical_retry_of_same_answer_does_not_increment(self) -> None:
        self.record("answer-1", missing=["ГОСТ 9.999-2024"])
        retried = self.record("answer-1", missing=["ГОСТ 9.999-2024"])
        self.assertEqual(0, retried["counter_increments"])
        self.assertEqual(1, retried["technical_duplicates_ignored"])
        demand = self.run_cli("demand-priorities", "--limit", "10")
        self.assertEqual(1, demand["results"][0]["potential_count"])

    def test_same_question_in_later_answers_increments_simple_counter(self) -> None:
        missing = ["ГОСТ 9.999-2024"]
        self.record("answer-user-a", missing=missing)
        self.record("answer-user-b", missing=missing)
        demand = self.run_cli("demand-priorities", "--limit", "10")
        self.assertEqual(2, demand["results"][0]["potential_count"])

    def test_existing_document_usage_increments_for_each_answer(self) -> None:
        self.record("answer-user-a", used=["НП-001-15"])
        self.record("answer-user-b", used=["НП-001-15"])
        usage = self.run_cli("document-usage", "--limit", "10")
        document = next(item for item in usage["results"] if item["document"] == "НП-001-15")
        self.assertEqual("loaded", document["status"])
        self.assertEqual(2, document["used_count"])

    def test_priority_is_simple_request_count(self) -> None:
        frequent = "ГОСТ 1.111-2025"
        structural = "ГОСТ 9.999-2024"
        self.record("answer-1", missing=[frequent, structural])
        self.record("answer-2", missing=[frequent])
        demand = self.run_cli("demand-priorities", "--limit", "10")
        self.assertEqual("ГОСТ 1.111-2025", demand["results"][0]["document"])
        self.assertEqual(2, demand["results"][0]["potential_count"])

    def test_loaded_document_leaves_active_demand_but_keeps_history(self) -> None:
        self.record("answer-1", missing=["ГОСТ 9.999-2024"])
        documents = yaml.safe_load(
            (self.root / "meta/documents.yaml").read_text(encoding="utf-8")
        )
        documents["documents"].append(
            {
                "id": "gost-9-999-2024",
                "short_title": "ГОСТ 9.999-2024",
                "title": "Добавленный стандарт",
                "canonical_exact": "gost:9.999:2024",
                "canonical_family": "gost:9.999",
            }
        )
        self._write_yaml(self.root / "meta/documents.yaml", documents)
        demand = self.run_cli("demand-priorities", "--limit", "10")
        self.assertEqual([], demand["results"])
        usage = self.run_cli("document-usage", "--limit", "10")
        historical = next(
            item for item in usage["results"] if item["document"] == "ГОСТ 9.999-2024"
        )
        self.assertEqual("loaded", historical["status"])
        self.assertEqual(1, historical["potential_count"])

    def test_single_table_has_one_row_per_document(self) -> None:
        self.record(
            "answer-1",
            used=["НП-001-15", "НП-001-15"],
            missing=["ГОСТ 9.999-2024", "ГОСТ 9.999-2024"],
        )
        with (self.state / "meta/document-usage.csv").open(
            "r", encoding="utf-8-sig", newline=""
        ) as stream:
            reader = csv.DictReader(stream, delimiter=";")
            rows = list(reader)
        self.assertEqual(2, len(rows))
        self.assertNotIn("reason", reader.fieldnames or [])
        self.assertNotIn("topic", reader.fieldnames or [])

    def test_one_row_keeps_potential_history_and_later_usage(self) -> None:
        document = "ГОСТ 9.999-2024"
        self.record("answer-before-upload", missing=[document])
        documents = yaml.safe_load(
            (self.root / "meta/documents.yaml").read_text(encoding="utf-8")
        )
        documents["documents"].append(
            {
                "id": "gost-9-999-2024",
                "short_title": document,
                "title": "Добавленный стандарт",
                "canonical_exact": "gost:9.999:2024",
                "canonical_family": "gost:9.999",
            }
        )
        self._write_yaml(self.root / "meta/documents.yaml", documents)
        self.record("answer-after-upload", used=[document])
        usage = self.run_cli("document-usage", "--limit", "10")
        matching = [item for item in usage["results"] if item["document"] == document]
        self.assertEqual(1, len(matching))
        self.assertEqual(1, matching[0]["potential_count"])
        self.assertEqual(1, matching[0]["used_count"])


if __name__ == "__main__":
    unittest.main()
