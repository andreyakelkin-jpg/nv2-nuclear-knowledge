import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import garant
import test_document_security as fixtures


def observation():
    return {"source_url": "https://internet.garant.ru/#/document/10105506/paragraph/138807:1",
            "title": "Тестовый экспорт", "checked_at": "2026-01-01T12:00:00+03:00",
            "provider_status": None, "observation_text": "Тестовый снимок состояния браузера."}


class ExportPickupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "state"
        self.downloads = Path(self.temp.name) / "Downloads"
        self.root.mkdir()
        self.downloads.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def begin(self):
        return Path(garant.begin_export(observation(), self.root, self.downloads, explicit_request=True)["ticket"])

    def collect(self, ticket, **kwargs):
        # Remove wall-clock waits while retaining the real two-snapshot stability check.
        with patch.object(garant.time, "sleep"):
            return garant.collect_export(ticket, self.root, explicit_request=True, **kwargs)

    def test_only_new_completed_export_is_collected_and_retry_is_idempotent(self):
        (self.downloads / "old.pdf").write_bytes(b"%PDF-1.4 old")
        ticket = self.begin()
        source = self.downloads / "Экспорт.pdf"
        source.write_bytes(b"%PDF-1.4 new")
        first = self.collect(ticket)
        second = self.collect(ticket)
        self.assertEqual(first["evidence_file"], second["evidence_file"])
        self.assertEqual(Path(first["download"]["file"]).read_bytes(), source.read_bytes())
        self.assertEqual(first["download"]["source_name"], source.name)

    def test_old_files_and_partial_export_do_not_enter_intake(self):
        (self.downloads / "old.pdf").write_bytes(b"%PDF-1.4 old")
        ticket = self.begin()
        (self.downloads / "new.pdf").write_bytes(b"%PDF-1.4 partial")
        (self.downloads / "new.pdf.crdownload").write_bytes(b"downloading")
        result = self.collect(ticket, wait_seconds=0)
        self.assertEqual(result["state"], "awaiting_export")
        self.assertFalse((self.root / "garant/downloads").exists())

    def test_no_export_is_not_success(self):
        result = self.collect(self.begin(), wait_seconds=0)
        self.assertFalse(result["archived"])

    def test_ambiguous_downloads_require_exact_observed_name(self):
        ticket = self.begin()
        for name in ("one.pdf", "two.pdf"):
            (self.downloads / name).write_bytes(b"%PDF-1.4 data")
        with self.assertRaisesRegex(ValueError, "Several"):
            self.collect(ticket)
        result = self.collect(ticket, expected_name="two.pdf")
        self.assertEqual(result["download"]["source_name"], "two.pdf")

    def test_growth_is_not_treated_as_completed(self):
        ticket = self.begin()
        snapshots = [{"growing.pdf": [10, 1]}, {"growing.pdf": [20, 2]}]
        with patch.object(garant, "file_snapshot", side_effect=snapshots), \
                patch.object(garant.time, "monotonic", side_effect=[0, 0, 0, 2]), \
                patch.object(garant.time, "sleep"):
            result = garant.collect_export(ticket, self.root, explicit_request=True, wait_seconds=1)
        self.assertEqual(result["state"], "awaiting_export")

    def test_explicit_request_and_ticket_scope(self):
        with self.assertRaises(ValueError):
            garant.begin_export(observation(), self.root, self.downloads, explicit_request=False)
        foreign = Path(self.temp.name) / "foreign.json"
        foreign.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "runtime state"):
            self.collect(foreign, wait_seconds=0)


class GarantArchiveIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.DocumentSecurityCliTests()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def prepare(self):
        f = self.fixture
        source = f.inputs / "browser-export.txt"
        source.write_text("Тестовый документ\n1. Требования к испытанию изделия.\n", encoding="utf-8")
        ticket = garant.begin_export(observation(), f.state, f.inputs, explicit_request=True)
        exported = f.inputs / "new-export.txt"
        exported.write_bytes(source.read_bytes())
        with patch.object(garant.time, "sleep"):
            receipt = garant.collect_export(Path(ticket["ticket"]), f.state, explicit_request=True)
        original = Path(receipt["download"]["file"])
        f.security_check(original)
        stage = f.stage(original)
        decision = f.prepare_decision(stage)
        return Path(receipt["evidence_file"]), original, stage, decision

    def test_export_security_stage_apply_publishes_original_and_provenance(self):
        f = self.fixture
        evidence, original, stage, decision = self.prepare()
        self.assertFalse(garant.import_status(evidence, f.state, f.root)["archived"])
        completed = f.run_raw("apply", str(decision), "--garant-evidence", str(evidence))
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        result = garant.import_status(evidence, f.state, f.root)
        self.assertTrue(result["archived"])
        self.assertEqual(len(result["documents"]), 1)
        doc = result["documents"][0]
        self.assertEqual(Path(doc["original"]).read_bytes(), original.read_bytes())
        card = Path(doc["card"]).read_text(encoding="utf-8")
        metadata = yaml.safe_load(card.split("---", 2)[1])
        self.assertEqual(metadata["source"]["garant"]["topic"], 10105506)
        self.assertFalse(metadata["source"]["garant"]["official_status_verified"])
        self.assertEqual(doc["lifecycle_stage"], "requires_expert_review")
        # A retried intake can observe the existing document without calling apply again.
        self.assertEqual(garant.import_status(evidence, f.state, f.root), result)

    def test_changed_collected_file_cannot_be_bound_to_another_staged_original(self):
        f = self.fixture
        evidence, original, stage, decision = self.prepare()
        original.write_text("Different export", encoding="utf-8")
        completed = f.run_raw("apply", str(decision), "--garant-evidence", str(evidence))
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(list((f.root / "docs").rglob("*.md")), [])

    def test_missing_security_report_still_blocks_archive(self):
        f = self.fixture
        evidence, original, stage, decision = self.prepare()
        (stage / "security-report.yaml").unlink()
        completed = f.run_raw("apply", str(decision), "--garant-evidence", str(evidence))
        self.assertNotEqual(completed.returncode, 0)
        self.assertFalse(garant.import_status(evidence, f.state, f.root)["archived"])


if __name__ == "__main__":
    unittest.main()
