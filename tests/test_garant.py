import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import garant


class GarantBrowserEvidenceTests(unittest.TestCase):
    def observation(self, **changes):
        return {"source_url": "https://internet.garant.ru/#/document/10103000",
                "title": "Тестовый документ", "checked_at": "2026-01-01T12:00:00+03:00",
                "provider_status": "Действует", "observation_text": "Наблюдаемый статус: Действует.", **changes}

    def test_no_record_without_explicit_request(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaises(ValueError):
                garant.record(self.observation(), root, explicit_request=False)
            self.assertEqual(list(root.iterdir()), [])

    def test_unknown_status_stays_unknown(self):
        report = garant.validate_observation(self.observation(provider_status=None))
        self.assertEqual(report["result"], "status_unknown")
        self.assertFalse(report["official_status_verified"])
        self.assertTrue(report["requires_expert_review"])

    def test_exact_provider_and_revision_status_preserved(self):
        report = garant.validate_observation(self.observation(provider_status="Утратил силу частично",
                                                            revision_status="Будущая редакция"))
        self.assertEqual(report["provider_status"], "Утратил силу частично")
        self.assertEqual(report["revision_status"], "Будущая редакция")
        self.assertEqual(report["evidence_level"], "reference_system")
        self.assertFalse(report["official_status_verified"])

    def test_reject_session_and_foreign_urls(self):
        for url in ("https://account.garant.ru/login?login_challenge=secret",
                    "https://internet.garant.ru/?token=secret#/document/10103000",
                    "https://internet.garant.ru.evil.test/#/document/10103000",
                    "https://user:secret@internet.garant.ru/#/document/10103000",
                    "https://internet.garant.ru/#/document/10103000?token=secret"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                garant.validate_observation(self.observation(source_url=url))

    def test_no_extra_credentials_or_approval_fields(self):
        for field in ("password", "cookies", "official_status_verified"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                garant.validate_observation(self.observation(**{field: "not-allowed"}))

    def test_observation_requires_timestamp_and_evidence(self):
        for changes in ({"checked_at": "2026-01-01T12:00:00"},
                        {"checked_at": "2999-01-01T12:00:00Z"}, {"observation_text": ""}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                garant.validate_observation(self.observation(**changes))

    def test_download_preserves_bytes_hash_and_prior_copies(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "browser.pdf"
            source.write_bytes(b"%PDF-1.4\noriginal-export")
            first = garant.record(self.observation(), root, explicit_request=True, source=source)
            second = garant.record(self.observation(), root, explicit_request=True, source=source)
            self.assertNotEqual(first["evidence_file"], second["evidence_file"])
            self.assertEqual(Path(first["download"]["file"]).read_bytes(), source.read_bytes())
            self.assertEqual(first["download"]["sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(first["download"]["security_status"], "not_checked")
            self.assertEqual(first["download"]["archive_status"], "not_archived")
            self.assertEqual(json.loads(Path(first["evidence_file"]).read_text(encoding="utf-8"))["topic"], 10103000)
            self.assertFalse((root / "docs").exists())
            self.assertFalse((root / "meta").exists())

    def test_html_error_page_cannot_be_pdf(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "login.pdf"
            source.write_bytes(b"<html>Sign in</html>")
            with self.assertRaises(ValueError):
                garant.record(self.observation(), root, explicit_request=True, source=source)
            self.assertFalse((root / "garant").exists())

    def test_file_limit_and_unsupported_format(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "document.txt"
            source.write_text("oversized", encoding="utf-8")
            with patch.object(garant, "MAX_FILE", 3), self.assertRaises(ValueError):
                garant.read_original(source)
            source = Path(folder) / "document.exe"
            source.write_bytes(b"binary")
            with self.assertRaises(ValueError):
                garant.read_original(source)

    def test_invalid_docx_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "document.docx"
            source.write_bytes(b"<html>Login</html>")
            with self.assertRaises(ValueError):
                garant.read_original(source)


if __name__ == "__main__":
    unittest.main()
