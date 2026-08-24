#!/usr/bin/env python3
"""Canonical per-document storage for detailed normative references."""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
REFERENCE_SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _yaml_bytes(data: dict[str, Any]) -> bytes:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100).encode("utf-8")


def _front_matter(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"Нет YAML-фронтматтера: {path}")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError(f"Не закрыт YAML-фронтматтер: {path}")
    metadata = yaml.safe_load(text[4:end]) or {}
    if not isinstance(metadata, dict):
        raise ValueError(f"YAML-фронтматтер должен быть объектом: {path}")
    return metadata, text[end + 5:]


def _write_document(path: Path, metadata: dict[str, Any], body: str) -> None:
    text = "---\n" + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False, width=100) + "---\n" + body
    path.write_text(text, encoding="utf-8")


def reference_path(root: Path, doc_id: str) -> Path:
    if not SAFE_ID.fullmatch(doc_id):
        raise ValueError(f"Некорректный document_id для графа ссылок: {doc_id!r}")
    return root / "relations" / "references" / f"{doc_id}.yaml"


def relative_reference_path(doc_id: str) -> str:
    if not SAFE_ID.fullmatch(doc_id):
        raise ValueError(f"Некорректный document_id для графа ссылок: {doc_id!r}")
    return f"relations/references/{doc_id}.yaml"


def _validated_graph_path(root: Path, doc_id: str, value: Any) -> Path:
    expected = reference_path(root, doc_id).resolve()
    candidate = (root / str(value or relative_reference_path(doc_id))).resolve()
    if candidate != expected:
        raise ValueError(
            f"reference_graph.file для {doc_id} должен быть {relative_reference_path(doc_id)}, получено {value!r}"
        )
    return candidate


def _references_from_payload(payload: dict[str, Any], doc_id: str, path: Path) -> list[dict[str, Any]]:
    if payload.get("schema_version") != REFERENCE_SCHEMA_VERSION:
        raise ValueError(f"Неподдерживаемая версия графа ссылок: {path}")
    if str(payload.get("document_id")) != doc_id:
        raise ValueError(f"document_id в графе ссылок не совпадает: {path}")
    references = payload.get("references", [])
    if not isinstance(references, list) or any(not isinstance(item, dict) for item in references):
        raise ValueError(f"references должен быть массивом объектов: {path}")
    return [dict(item) for item in references]


def load_references(
    root: Path,
    metadata: dict[str, Any],
    *,
    allow_legacy: bool = True,
    validate_summary: bool = True,
) -> list[dict[str, Any]]:
    doc_id = str(metadata.get("id", ""))
    legacy = metadata.get("references")
    graph = metadata.get("reference_graph")
    graph_references: list[dict[str, Any]] | None = None
    if graph is not None:
        if not isinstance(graph, dict):
            raise ValueError(f"reference_graph карточки {doc_id} должен быть объектом")
        path = _validated_graph_path(root, doc_id, graph.get("file"))
        if not path.is_file():
            raise FileNotFoundError(f"Не найден граф ссылок карточки {doc_id}: {path}")
        graph_references = _references_from_payload(read_yaml(path), doc_id, path)
        if validate_summary:
            expected = reference_summary(root, doc_id, graph_references)
            if graph != expected:
                raise ValueError(f"Компактная сводка reference_graph не совпадает с файлом: {doc_id}")
    if legacy is not None:
        if not allow_legacy:
            raise ValueError(f"Карточка {doc_id} содержит устаревшее поле references")
        if not isinstance(legacy, list) or any(not isinstance(item, dict) for item in legacy):
            raise ValueError(f"references карточки {doc_id} должен быть массивом объектов")
        legacy_references = [dict(item) for item in legacy]
        if graph_references is not None and graph_references != legacy_references:
            raise ValueError(f"Карточка и внешний граф ссылок расходятся: {doc_id}")
        return legacy_references
    return graph_references or []


def write_references(root: Path, doc_id: str, references: list[dict[str, Any]]) -> tuple[Path, bool]:
    if any(not isinstance(item, dict) for item in references):
        raise ValueError("Каждая нормативная ссылка должна быть объектом")
    path = reference_path(root, doc_id)
    existing = read_yaml(path)
    existing_references: list[dict[str, Any]] | None = None
    if existing:
        existing_references = _references_from_payload(existing, doc_id, path)
    if existing_references == references:
        return path, False
    payload = {
        "schema_version": REFERENCE_SCHEMA_VERSION,
        "document_id": doc_id,
        "updated_at": utc_now(),
        "references": references,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_yaml_bytes(payload))
    return path, True


def reference_summary(root: Path, doc_id: str, references: list[dict[str, Any]]) -> dict[str, Any]:
    path = reference_path(root, doc_id)
    if not path.is_file():
        raise FileNotFoundError(f"Не найден граф ссылок: {path}")
    payload = read_yaml(path)
    statuses = Counter(str(item.get("status") or "не_указан") for item in references)
    roles = Counter(str(item.get("reference_role") or "other") for item in references)
    unresolved_statuses = {"отсутствует", "устарел_нет_замены"}
    unresolved = sum(1 for item in references if item.get("status") in unresolved_statuses or item.get("requires_analysis"))
    return {
        "file": relative_reference_path(doc_id),
        "count": len(references),
        "unresolved": unresolved,
        "by_status": dict(sorted(statuses.items())),
        "by_role": dict(sorted(roles.items())),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "updated_at": payload.get("updated_at"),
    }


def attach_reference_graph(root: Path, metadata: dict[str, Any], references: list[dict[str, Any]]) -> bool:
    doc_id = str(metadata.get("id", ""))
    before = metadata.get("reference_graph")
    metadata.pop("references", None)
    metadata["reference_graph"] = reference_summary(root, doc_id, references)
    return before != metadata["reference_graph"]


def externalize_document(root: Path, path: Path, *, write: bool = True) -> dict[str, Any]:
    metadata, body = _front_matter(path)
    doc_id = str(metadata.get("id", ""))
    had_legacy = "references" in metadata
    references = load_references(root, metadata, allow_legacy=True, validate_summary=False)
    graph_path = reference_path(root, doc_id)
    graph_changed = False
    if write:
        graph_path, graph_changed = write_references(root, doc_id, references)
        metadata_changed = attach_reference_graph(root, metadata, references) or had_legacy
        if metadata_changed:
            _write_document(path, metadata, body)
    else:
        graph_changed = not graph_path.is_file()
        metadata_changed = had_legacy or "reference_graph" not in metadata or not graph_path.is_file()
    return {
        "document_id": doc_id,
        "references": len(references),
        "legacy_removed": had_legacy,
        "card_changed": metadata_changed,
        "graph_changed": graph_changed,
        "graph_path": graph_path,
    }


def externalize_all(root: Path, *, write: bool = True) -> dict[str, Any]:
    results = []
    for path in sorted((root / "docs").rglob("*.md")):
        if "_templates" in path.parts:
            continue
        results.append(externalize_document(root, path, write=write))
    return {
        "documents": len(results),
        "references": sum(item["references"] for item in results),
        "legacy_cards": sum(1 for item in results if item["legacy_removed"]),
        "changed_cards": sum(1 for item in results if item["card_changed"]),
        "changed_graphs": sum(1 for item in results if item["graph_changed"]),
        "results": results,
    }
