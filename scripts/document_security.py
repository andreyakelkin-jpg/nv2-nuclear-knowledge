#!/usr/bin/env python3
"""Fail-closed document security contract for the NV2 archive workflow.

The module never executes or renders document content. It performs bounded
container/file checks and combines them with two independently supplied
reports: a local malware scanner result and a Codex semantic review produced
without tools or side effects.
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import yaml


SECURITY_SCHEMA_VERSION = 1
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}
MAX_SOURCE_BYTES = 50 * 1024 * 1024
MAX_REPORT_BYTES = 1024 * 1024
MAX_ZIP_ENTRIES = 5000
MAX_ZIP_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_ZIP_RATIO = 200
STATUS_RANK = {"passed": 0, "review_required": 1, "rejected": 2}
FINAL_STATUS = {
    "passed": "security_passed",
    "review_required": "security_review_required",
    "rejected": "security_rejected",
}
PROMPT_INJECTION_PATTERNS = (
    ("prompt_injection", re.compile(r"\bignore\s+(?:all\s+)?(?:previous|prior|system)\s+instructions?\b", re.I)),
    ("prompt_injection", re.compile(r"\b(?:reveal|print|show)\s+(?:the\s+)?system\s+prompt\b", re.I)),
    ("prompt_injection", re.compile(r"игнорир(?:уй|овать)\S*\s+(?:все\s+)?(?:предыдущ\S*|системн\S*)\s+инструкц", re.I)),
    ("secret_request", re.compile(r"\b(?:api[_ -]?key|access[_ -]?token|private[_ -]?key|password)\b", re.I)),
    ("destructive_command", re.compile(r"\brm\s+-(?=[a-z]*r)(?=[a-z]*f)[a-z]+\b", re.I)),
    ("destructive_command", re.compile(r"\bremove-item\b[^\r\n]{0,120}\b-recurse\b", re.I)),
    ("destructive_command", re.compile(r"\b(?:drop\s+(?:database|table)|truncate\s+table|delete\s+from)\b", re.I)),
    ("destructive_command", re.compile(r"\bудал(?:и|ить|ение)\S*\s+(?:все\S*|файл\S*|баз\S*|данн\S*)", re.I)),
)
PDF_REJECT_MARKERS = {
    b"/javascript": "pdf_javascript",
    b"/js": "pdf_javascript",
    b"/launch": "pdf_launch_action",
    b"/embeddedfile": "pdf_embedded_file",
    b"/submitform": "pdf_submit_form",
    b"/importdata": "pdf_import_data",
    b"/richmedia": "pdf_rich_media",
}
PDF_REVIEW_MARKERS = {
    b"/openaction": "pdf_open_action",
    b"/aa": "pdf_additional_action",
    b"/uri": "pdf_external_uri",
    b"/encrypt": "pdf_encrypted",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _finding(code: str, severity: str, message: str) -> dict[str, str]:
    return {"code": code, "severity": severity, "message": message}


def _combined_status(*statuses: str) -> str:
    return max(statuses, key=lambda value: STATUS_RANK[value])


def _status_from_findings(findings: list[dict[str, str]]) -> str:
    if any(item["severity"] == "rejected" for item in findings):
        return "rejected"
    if findings:
        return "review_required"
    return "passed"


def _read_yaml_report(path: Path, label: str) -> dict[str, Any]:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"{label} не найден: {path}")
    if path.stat().st_size > MAX_REPORT_BYTES:
        raise ValueError(f"{label} превышает допустимый размер {MAX_REPORT_BYTES} байт")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{label} должен быть YAML-объектом")
    return loaded


def _validate_source(path: Path) -> tuple[Path, str, int]:
    source = path.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Файл не найден: {source}")
    size = source.stat().st_size
    if size <= 0:
        raise ValueError("Пустой документ не допускается")
    if size > MAX_SOURCE_BYTES:
        raise ValueError(f"Документ превышает допустимый размер {MAX_SOURCE_BYTES} байт")
    if source.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError("Допустимы только PDF, DOCX, TXT и MD")
    if len(source.name) > 255 or any(ord(character) < 32 for character in source.name):
        raise ValueError("Имя документа содержит недопустимые символы или слишком длинное")
    return source, sha256_file(source), size


def _inspect_docx(source: Path, findings: list[dict[str, str]]) -> None:
    if not zipfile.is_zipfile(source):
        findings.append(_finding("docx_invalid_container", "rejected", "DOCX не является корректным ZIP-контейнером"))
        return
    with zipfile.ZipFile(source) as archive:
        entries = archive.infolist()
        names = {item.filename.lower() for item in entries}
        if "[content_types].xml" not in names or "word/document.xml" not in names:
            findings.append(_finding("docx_missing_structure", "rejected", "В DOCX отсутствует обязательная структура Word"))
        if len(entries) > MAX_ZIP_ENTRIES:
            findings.append(_finding("docx_entry_limit", "rejected", "DOCX превышает лимит числа ZIP-записей"))
        total_uncompressed = 0
        for entry in entries:
            normalized = entry.filename.replace("\\", "/")
            pure = PurePosixPath(normalized)
            if pure.is_absolute() or ".." in pure.parts:
                findings.append(_finding("docx_path_traversal", "rejected", "DOCX содержит небезопасный путь архива"))
            total_uncompressed += entry.file_size
            if entry.file_size and entry.file_size / max(entry.compress_size, 1) > MAX_ZIP_RATIO:
                findings.append(_finding("docx_compression_ratio", "rejected", "DOCX содержит аномально сжатую ZIP-запись"))
            lowered = normalized.lower()
            if lowered.endswith("vbaproject.bin"):
                findings.append(_finding("docx_macro", "rejected", "DOCX содержит VBA-макрос"))
            if "/embeddings/" in lowered or lowered.endswith((".bin", ".exe", ".dll", ".js", ".vbs", ".ps1")):
                findings.append(_finding("docx_embedded_object", "rejected", "DOCX содержит исполняемый или внедрённый объект"))
        if total_uncompressed > MAX_ZIP_UNCOMPRESSED_BYTES:
            findings.append(_finding("docx_uncompressed_limit", "rejected", "DOCX превышает лимит распакованного размера"))
        for entry in entries:
            if not entry.filename.lower().endswith(".rels") or entry.file_size > MAX_REPORT_BYTES:
                continue
            content = archive.read(entry).lower()
            if b'targetmode="external"' in content or b"targetmode='external'" in content:
                findings.append(_finding("docx_external_relationship", "review_required", "DOCX содержит внешнюю связь"))


def inspect_source(source_path: Path) -> dict[str, Any]:
    source, digest, size = _validate_source(source_path)
    findings: list[dict[str, str]] = []
    suffix = source.suffix.lower()
    content = source.read_bytes()
    if suffix == ".pdf":
        if not content.startswith(b"%PDF-"):
            findings.append(_finding("pdf_magic_mismatch", "rejected", "Расширение PDF не совпадает с сигнатурой файла"))
        lowered = content.lower()
        for marker, code in PDF_REJECT_MARKERS.items():
            if marker in lowered:
                findings.append(_finding(code, "rejected", f"PDF содержит активный элемент {marker.decode('ascii')}"))
        for marker, code in PDF_REVIEW_MARKERS.items():
            if marker in lowered:
                findings.append(_finding(code, "review_required", f"PDF содержит требующий проверки элемент {marker.decode('ascii')}"))
    elif suffix == ".docx":
        _inspect_docx(source, findings)
    else:
        if b"\x00" in content:
            findings.append(_finding("text_nul_byte", "rejected", "Текстовый файл содержит NUL-байты"))
            text = ""
        else:
            try:
                text = content.decode("utf-8-sig")
            except UnicodeDecodeError:
                findings.append(_finding("text_encoding", "rejected", "TXT/MD должен иметь корректную UTF-8 кодировку"))
                text = ""
        for code, pattern in PROMPT_INJECTION_PATTERNS:
            if pattern.search(text):
                findings.append(_finding(code, "review_required", "Текст содержит потенциальную инструкцию агенту или опасную команду"))
    return {
        "status": _status_from_findings(findings),
        "source_sha256": digest,
        "source_size": size,
        "source_extension": suffix,
        "findings": findings,
        "limits": {
            "max_source_bytes": MAX_SOURCE_BYTES,
            "max_zip_entries": MAX_ZIP_ENTRIES,
            "max_zip_uncompressed_bytes": MAX_ZIP_UNCOMPRESSED_BYTES,
            "max_zip_ratio": MAX_ZIP_RATIO,
        },
    }


def _validate_scanner_report(payload: dict[str, Any], digest: str) -> dict[str, Any]:
    if payload.get("schema_version") != SECURITY_SCHEMA_VERSION:
        raise ValueError("Отчёт антивирусного сканера имеет неподдерживаемую версию схемы")
    if payload.get("source_sha256") != digest:
        raise ValueError("SHA-256 в отчёте антивирусного сканера не совпадает с документом")
    scanner = str(payload.get("scanner", "")).strip()
    scanner_version = str(payload.get("scanner_version", "")).strip()
    status = str(payload.get("status", "")).strip()
    if not scanner or not scanner_version:
        raise ValueError("В отчёте антивирусного сканера отсутствуют scanner или scanner_version")
    if status not in {"clean", "infected", "unavailable", "error"}:
        raise ValueError("Некорректный status отчёта антивирусного сканера")
    mapped = {"clean": "passed", "infected": "rejected", "unavailable": "review_required", "error": "review_required"}[status]
    findings = payload.get("findings", [])
    if not isinstance(findings, list):
        raise ValueError("findings отчёта антивирусного сканера должен быть массивом")
    if status == "clean" and findings:
        mapped = "review_required"
    return {
        "status": mapped,
        "scanner": scanner,
        "scanner_version": scanner_version,
        "scanner_status": status,
        "findings": findings,
    }


def _validate_semantic_report(payload: dict[str, Any], digest: str) -> dict[str, Any]:
    if payload.get("schema_version") != SECURITY_SCHEMA_VERSION:
        raise ValueError("Семантический отчёт Codex имеет неподдерживаемую версию схемы")
    if payload.get("source_sha256") != digest:
        raise ValueError("SHA-256 в семантическом отчёте Codex не совпадает с документом")
    if str(payload.get("assessor", "")).strip().lower() != "codex":
        raise ValueError("Семантический отчёт должен быть подготовлен Codex")
    model = str(payload.get("model", "")).strip()
    if not model:
        raise ValueError("В семантическом отчёте отсутствует модель Codex")
    isolation = {
        "content_mode": payload.get("content_mode"),
        "tool_access": payload.get("tool_access"),
        "network_access": payload.get("network_access"),
        "secrets_access": payload.get("secrets_access"),
        "side_effects": payload.get("side_effects"),
    }
    if isolation != {
        "content_mode": "extracted_text",
        "tool_access": "none",
        "network_access": "none",
        "secrets_access": "none",
        "side_effects": "none",
    }:
        raise ValueError("Семантическая проверка Codex должна быть изолирована от инструментов, сети и секретов")
    status = str(payload.get("status", "")).strip()
    if status not in STATUS_RANK:
        raise ValueError("Некорректный status семантического отчёта Codex")
    findings = payload.get("findings", [])
    if not isinstance(findings, list):
        raise ValueError("findings семантического отчёта Codex должен быть массивом")
    if status == "passed" and findings:
        status = "review_required"
    return {
        "status": status,
        "assessor": "codex",
        "model": model,
        **isolation,
        "findings": findings,
    }


def security_report_path(state_root: Path, digest: str) -> Path:
    return state_root.resolve() / "reports" / "document-security" / f"{digest}.yaml"


def _atomic_write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as stream:
        yaml.safe_dump(payload, stream, allow_unicode=True, sort_keys=False, width=100)
        temporary = Path(stream.name)
    try:
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def create_security_report(
    source_path: Path,
    scanner_report_path: Path,
    semantic_report_path: Path,
    state_root: Path,
) -> tuple[Path, dict[str, Any]]:
    source, digest, size = _validate_source(source_path)
    deterministic = inspect_source(source)
    scanner = _validate_scanner_report(_read_yaml_report(scanner_report_path, "Отчёт антивирусного сканера"), digest)
    semantic = _validate_semantic_report(_read_yaml_report(semantic_report_path, "Семантический отчёт Codex"), digest)
    status = _combined_status(deterministic["status"], scanner["status"], semantic["status"])
    payload = {
        "schema_version": SECURITY_SCHEMA_VERSION,
        "report_id": hashlib.sha256(f"{digest}:{utc_now()}".encode("utf-8")).hexdigest()[:24],
        "created_at": utc_now(),
        "source": {"name": source.name, "sha256": digest, "size": size, "extension": source.suffix.lower()},
        "checks": {"deterministic": deterministic, "malware": scanner, "semantic": semantic},
        "verdict": {
            "status": FINAL_STATUS[status],
            "fail_closed": True,
            "allows_archive": status == "passed",
        },
    }
    output = security_report_path(state_root, digest)
    _atomic_write_yaml(output, payload)
    return output, payload


def validate_security_report(
    source_path: Path,
    report_path: Path,
    *,
    allowed_root: Path | None = None,
    require_passed: bool = True,
) -> dict[str, Any]:
    source, digest, size = _validate_source(source_path)
    report = report_path.expanduser().resolve()
    if allowed_root is not None:
        root = allowed_root.expanduser().resolve()
        if report != root and root not in report.parents:
            raise ValueError("Security report находится вне разрешённого каталога")
    payload = _read_yaml_report(report, "Security report")
    if payload.get("schema_version") != SECURITY_SCHEMA_VERSION:
        raise ValueError("Security report имеет неподдерживаемую версию схемы")
    source_payload = payload.get("source")
    if not isinstance(source_payload, dict):
        raise ValueError("В security report отсутствует блок source")
    if source_payload.get("sha256") != digest or source_payload.get("size") != size:
        raise ValueError("Security report относится к другому содержимому документа")
    checks = payload.get("checks")
    if not isinstance(checks, dict) or set(checks) != {"deterministic", "malware", "semantic"}:
        raise ValueError("Security report не содержит все обязательные проверки")
    deterministic = checks["deterministic"]
    malware = checks["malware"]
    semantic = checks["semantic"]
    if not all(isinstance(item, dict) for item in (deterministic, malware, semantic)):
        raise ValueError("Security report содержит некорректные блоки проверок")
    repeated_deterministic = inspect_source(source)
    if deterministic != repeated_deterministic:
        raise ValueError("Детерминированная проверка security report не совпадает с текущим документом")
    scanner_status = malware.get("scanner_status")
    expected_malware_status = {
        "clean": "passed",
        "infected": "rejected",
        "unavailable": "review_required",
        "error": "review_required",
    }.get(scanner_status)
    if scanner_status == "clean" and malware.get("findings"):
        expected_malware_status = "review_required"
    if not expected_malware_status or malware.get("status") != expected_malware_status:
        raise ValueError("Security report содержит противоречивый результат антивирусного сканера")
    if not malware.get("scanner") or not malware.get("scanner_version"):
        raise ValueError("Security report не идентифицирует антивирусный сканер")
    if (
        semantic.get("status") not in STATUS_RANK
        or semantic.get("assessor") != "codex"
        or not semantic.get("model")
        or semantic.get("content_mode") != "extracted_text"
        or semantic.get("tool_access") != "none"
        or semantic.get("network_access") != "none"
        or semantic.get("secrets_access") != "none"
        or semantic.get("side_effects") != "none"
    ):
        raise ValueError("Security report содержит некорректный семантический результат Codex")
    expected_status = FINAL_STATUS[
        _combined_status(
            deterministic["status"],
            str(malware["status"]),
            str(semantic["status"]),
        )
    ]
    verdict = payload.get("verdict")
    if not isinstance(verdict, dict) or verdict.get("status") not in set(FINAL_STATUS.values()):
        raise ValueError("Security report не содержит допустимый verdict")
    if verdict.get("fail_closed") is not True:
        raise ValueError("Security report не подтверждает fail-closed режим")
    if verdict.get("status") != expected_status:
        raise ValueError("Итоговый verdict security report не совпадает с результатами проверок")
    if verdict.get("allows_archive") is not (expected_status == "security_passed"):
        raise ValueError("Security report содержит противоречивое разрешение архивирования")
    if require_passed and (verdict.get("status") != "security_passed" or verdict.get("allows_archive") is not True):
        raise ValueError(f"Документ не прошёл security-gate: {verdict.get('status')}")
    return payload
