#!/usr/bin/env python3
"""Remove active actions and external transitions from an untrusted PDF.

The sanitizer never overwrites the source. It preserves page content and
requires fresh malware and semantic evidence for the resulting SHA-256.
"""
from __future__ import annotations

import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject

from document_security import MAX_SOURCE_BYTES, inspect_source, sha256_file


SANITIZABLE_FINDING_CODES = {
    "pdf_javascript",
    "pdf_external_uri",
    "pdf_launch_action",
    "pdf_submit_form",
    "pdf_import_data",
    "pdf_remote_goto",
    "pdf_open_action",
    "pdf_additional_action",
}

ACTIVE_ACTION_TYPES = {
    "/javascript": "pdf_javascript",
    "/uri": "pdf_external_uri",
    "/launch": "pdf_launch_action",
    "/submitform": "pdf_submit_form",
    "/importdata": "pdf_import_data",
    "/gotor": "pdf_remote_goto",
}

DIRECT_ACTIVE_KEYS = {
    "/js": "pdf_javascript",
    "/javascript": "pdf_javascript",
    "/uri": "pdf_external_uri",
    "/openaction": "pdf_open_action",
    "/aa": "pdf_additional_action",
}


def _resolve(value: Any) -> Any:
    if isinstance(value, IndirectObject):
        return value.get_object()
    return value


def _active_action_code(value: Any) -> str | None:
    value = _resolve(value)
    if not isinstance(value, DictionaryObject):
        return None
    action_type = str(_resolve(value.get("/S", ""))).lower()
    return ACTIVE_ACTION_TYPES.get(action_type)


def _sanitize_object(value: Any, removed: Counter[str], visited: set[int]) -> None:
    value = _resolve(value)
    if isinstance(value, (DictionaryObject, ArrayObject)):
        identity = id(value)
        if identity in visited:
            return
        visited.add(identity)
    if isinstance(value, ArrayObject):
        for item in list(value):
            _sanitize_object(item, removed, visited)
        return
    if not isinstance(value, DictionaryObject):
        return

    for key in list(value.keys()):
        lowered = str(key).lower()
        if lowered in DIRECT_ACTIVE_KEYS:
            removed[DIRECT_ACTIVE_KEYS[lowered]] += 1
            del value[key]
            continue
        nested = _resolve(value[key])
        action_code = _active_action_code(nested)
        if action_code:
            removed[action_code] += 1
            del value[key]
            continue
        _sanitize_object(nested, removed, visited)


def _page_text(reader: PdfReader) -> list[str]:
    return [(page.extract_text() or "") for page in reader.pages]


def _default_output(state_root: Path, source: Path, digest: str) -> Path:
    safe_stem = re.sub(r"[^0-9A-Za-zА-Яа-яЁё._-]+", "-", source.stem).strip("-.") or "document"
    return (
        state_root.resolve()
        / "sanitized-documents"
        / digest[:2]
        / digest
        / f"{safe_stem}_sanitized.pdf"
    )


def sanitize_pdf(
    source_path: Path,
    state_root: Path,
    output_path: Path | None = None,
) -> dict[str, Any]:
    source = source_path.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"PDF не найден: {source}")
    if source.suffix.lower() != ".pdf":
        raise ValueError("Очистка активного содержимого поддерживается только для PDF")
    if source.stat().st_size <= 0 or source.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("Размер PDF вне допустимого диапазона")

    original_digest = sha256_file(source)
    initial = inspect_source(source)
    finding_codes = {str(item.get("code")) for item in initial["findings"]}
    sanitizable = finding_codes & SANITIZABLE_FINDING_CODES
    unsupported = finding_codes - SANITIZABLE_FINDING_CODES
    if unsupported:
        raise ValueError(
            "PDF содержит элементы, которые нельзя автоматически очистить: "
            + ", ".join(sorted(unsupported))
        )
    if not sanitizable:
        raise ValueError("PDF не содержит поддерживаемых активных действий или внешних URI")

    target = (
        output_path.expanduser().resolve()
        if output_path is not None
        else _default_output(state_root, source, original_digest)
    )
    if target == source:
        raise ValueError("Исходный PDF нельзя перезаписывать")
    target.parent.mkdir(parents=True, exist_ok=True)

    reader = PdfReader(str(source), strict=False)
    if reader.is_encrypted:
        raise ValueError("Зашифрованный PDF нельзя автоматически очистить")
    before_text = _page_text(reader)

    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    removed: Counter[str] = Counter()
    _sanitize_object(writer._root_object, removed, set())
    if not removed:
        raise ValueError("Активные элементы обнаружены, но безопасно удалить их не удалось")

    with tempfile.NamedTemporaryFile(
        "wb", dir=target.parent, delete=False, suffix=".pdf"
    ) as stream:
        temporary = Path(stream.name)
        writer.write(stream)
    try:
        sanitized_reader = PdfReader(str(temporary), strict=True)
        after_text = _page_text(sanitized_reader)
        if len(sanitized_reader.pages) != len(reader.pages):
            raise ValueError("После очистки изменилось число страниц PDF")
        if before_text != after_text:
            raise ValueError("После очистки изменился извлекаемый текст PDF")
        remaining = inspect_source(temporary)
        if remaining["status"] != "passed":
            codes = ", ".join(str(item.get("code")) for item in remaining["findings"])
            raise ValueError(f"После очистки PDF не прошёл контейнерную проверку: {codes}")
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()

    if sha256_file(source) != original_digest:
        raise RuntimeError("Исходный PDF изменился во время очистки")
    return {
        "status": "sanitized",
        "source": str(source),
        "source_sha256": original_digest,
        "sanitized_source": str(target),
        "sanitized_sha256": sha256_file(target),
        "original_preserved": True,
        "pages": len(reader.pages),
        "text_preserved": True,
        "removed": dict(sorted(removed.items())),
        "security_evidence_reusable": False,
        "next_step": (
            "Создайте новые scanner/semantic reports для sanitized_source, "
            "затем повторите kb security-check и архивируйте только очищенную копию."
        ),
    }
