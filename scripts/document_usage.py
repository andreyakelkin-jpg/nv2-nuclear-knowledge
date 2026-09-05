#!/usr/bin/env python3
"""Maintain one simple table of used and potentially needed documents."""
from __future__ import annotations

import csv
import os
import re
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

from reference_resolver import canonical_identifier, identifier_from_id, normalize


OBSERVATION_SCHEMA_VERSION = 1
ANSWER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
TABLE_PATH = Path("meta/document-usage.csv")
TABLE_FIELDS = (
    "demand_rank",
    "usage_rank",
    "canonical_key",
    "family_key",
    "document_ids",
    "document",
    "status",
    "used_count",
    "potential_count",
    "first_used_at",
    "last_used_at",
    "first_needed_at",
    "last_needed_at",
    "last_used_answer_id",
    "last_potential_answer_id",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _clean_text(value: Any, field: str, maximum: int, *, required: bool = True) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if required and not text:
        raise ValueError(f"{field}: поле обязательно")
    if len(text) > maximum:
        raise ValueError(f"{field}: не более {maximum} символов")
    return text


def document_key(label: str, document_id: str = "") -> tuple[str, str | None]:
    label_exact, label_family = canonical_identifier(label)
    id_exact, id_family = identifier_from_id(document_id) if document_id else (None, None)
    exact = label_exact or id_exact
    family = label_family or id_family
    if exact or family:
        return str(exact or family), str(family) if family else None
    if document_id:
        return f"id:{document_id.lower()}", None
    return "text:" + normalize(label).lower(), None


def _normalize_document(item: Any, field: str) -> dict[str, str | None]:
    if isinstance(item, str):
        item = {"document": item}
    if not isinstance(item, dict):
        raise ValueError(f"{field} должен быть строкой или объектом")
    label = _clean_text(
        item.get("document") or item.get("document_label"),
        f"{field}.document",
        300,
    )
    document_id = _clean_text(
        item.get("document_id"), f"{field}.document_id", 160, required=False
    )
    canonical_key, family_key = document_key(label, document_id)
    return {
        "document_id": document_id,
        "canonical_key": canonical_key,
        "family_key": family_key,
        "document": label,
    }


def validate_observation(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != OBSERVATION_SCHEMA_VERSION:
        raise ValueError(f"schema_version должен быть равен {OBSERVATION_SCHEMA_VERSION}")
    answer_id = _clean_text(payload.get("answer_id"), "answer_id", 128)
    if not ANSWER_ID.fullmatch(answer_id):
        raise ValueError(
            "answer_id: допустимы латинские буквы, цифры, точка, двоеточие, дефис и подчёркивание"
        )
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[bool, str]] = set()
    for field_name, missing in (
        ("used_documents", False),
        ("missing_documents", True),
    ):
        items = payload.get(field_name, [])
        if not isinstance(items, list):
            raise ValueError(f"{field_name} должен быть списком")
        for number, item in enumerate(items, 1):
            document = _normalize_document(item, f"{field_name}[{number}]")
            key = (missing, str(document["canonical_key"]))
            if key in seen:
                continue
            seen.add(key)
            document["missing"] = missing
            normalized.append(document)
    if not normalized:
        raise ValueError("Укажите хотя бы один использованный или недостающий документ")
    return {"answer_id": answer_id, "documents": normalized}


def _read_table(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return [dict(row) for row in csv.DictReader(stream, delimiter=";")]


def _write_table(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(
                stream, fieldnames=list(TABLE_FIELDS), delimiter=";", lineterminator="\n"
            )
            writer.writeheader()
            writer.writerows(rows)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return loaded if isinstance(loaded, dict) else {}


def _split_values(value: Any) -> set[str]:
    return {item.strip() for item in str(value or "").split("|") if item.strip()}


def _join_values(values: Iterable[str]) -> str:
    return " | ".join(sorted({str(value).strip() for value in values if str(value).strip()}))


def _loaded_document_maps(
    kb_root: Path,
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    exact: dict[str, set[str]] = defaultdict(set)
    families: dict[str, set[str]] = defaultdict(set)
    for item in _read_yaml(kb_root / "meta" / "documents.yaml").get("documents", []):
        if not isinstance(item, dict):
            continue
        document_id = str(item.get("id") or "").strip()
        if not document_id:
            continue
        exact[f"id:{document_id.lower()}"].add(document_id)
        candidates: list[tuple[str | None, str | None]] = [
            (item.get("canonical_exact"), item.get("canonical_family")),
            identifier_from_id(document_id),
        ]
        for field in ("short_title", "title"):
            if item.get(field):
                value = str(item[field])
                candidates.append(canonical_identifier(value))
                exact["text:" + normalize(value).lower()].add(document_id)
        for exact_key, family_key in candidates:
            if exact_key:
                exact[str(exact_key)].add(document_id)
            if family_key:
                families[str(family_key)].add(document_id)
    return exact, families


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _refresh_rows(rows: list[dict[str, str]], kb_root: Path) -> list[dict[str, Any]]:
    loaded_exact, loaded_families = _loaded_document_maps(kb_root)
    refreshed: list[dict[str, Any]] = []
    for source in rows:
        row: dict[str, Any] = {field: source.get(field, "") for field in TABLE_FIELDS}
        key = str(row.get("canonical_key") or "")
        family_key = str(row.get("family_key") or "")
        document_ids = _split_values(row.get("document_ids"))
        document_ids.update(loaded_exact.get(key, set()))
        if not document_ids and key == family_key:
            document_ids.update(loaded_families.get(key, set()))
        row["document_ids"] = _join_values(document_ids)
        row["used_count"] = _int(row.get("used_count"))
        row["potential_count"] = _int(row.get("potential_count"))
        row["status"] = "loaded" if document_ids else "needed" if row["potential_count"] else "observed"
        refreshed.append(row)

    usage_order = list(refreshed)
    usage_order.sort(key=lambda row: str(row.get("document") or ""))
    usage_order.sort(key=lambda row: str(row.get("last_used_at") or ""), reverse=True)
    usage_order.sort(key=lambda row: _int(row["used_count"]), reverse=True)
    for rank, row in enumerate(usage_order, 1):
        row["usage_rank"] = rank

    demand_order = [
        row
        for row in refreshed
        if row["status"] == "needed" and _int(row["potential_count"]) > 0
    ]
    demand_order.sort(key=lambda row: str(row.get("document") or ""))
    demand_order.sort(key=lambda row: str(row.get("last_needed_at") or ""), reverse=True)
    demand_order.sort(key=lambda row: _int(row["potential_count"]), reverse=True)
    demand_rank = {str(row["canonical_key"]): rank for rank, row in enumerate(demand_order, 1)}
    for row in refreshed:
        row["demand_rank"] = demand_rank.get(str(row["canonical_key"]), "")

    refreshed.sort(
        key=lambda row: (
            row["demand_rank"] == "",
            _int(row["demand_rank"]) if row["demand_rank"] != "" else _int(row["usage_rank"]),
            str(row.get("document") or ""),
        )
    )
    return refreshed


def _public_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "document": row.get("document"),
        "document_ids": sorted(_split_values(row.get("document_ids"))),
        "status": row.get("status"),
        "used_count": _int(row.get("used_count")),
        "potential_count": _int(row.get("potential_count")),
        "first_used_at": row.get("first_used_at") or None,
        "last_used_at": row.get("last_used_at") or None,
        "first_needed_at": row.get("first_needed_at") or None,
        "last_needed_at": row.get("last_needed_at") or None,
    }


def document_usage_statistics(state_root: Path, kb_root: Path) -> dict[str, Any]:
    table_path = state_root / TABLE_PATH
    rows = _refresh_rows(_read_table(table_path), kb_root)
    usage = sorted(rows, key=lambda row: _int(row["usage_rank"]))
    demand = sorted(
        [row for row in rows if row["demand_rank"] != ""],
        key=lambda row: _int(row["demand_rank"]),
    )
    return {
        "generated_at": utc_now(),
        "counting_rule": "Один документ получает не более +1 каждого счётчика за один ответ; новое обращение считается снова.",
        "ranking_basis": "Очередь загрузки сортируется по potential_count — числу отдельных ответов, которым не хватило документа.",
        "document_count": len(rows),
        "active_demand_count": len(demand),
        "documents": [_public_row(row) for row in usage],
        "demand": [_public_row(row) for row in demand],
        "table_path": str(table_path),
    }


def record_document_usage(
    state_root: Path,
    kb_root: Path,
    payload: dict[str, Any],
) -> dict[str, Any]:
    observation = validate_observation(payload)
    table_path = state_root / TABLE_PATH
    rows = _refresh_rows(_read_table(table_path), kb_root)
    by_key = {str(row.get("canonical_key")): row for row in rows}
    recorded_at = utc_now()
    increments = 0
    duplicates = 0
    for document in observation["documents"]:
        key = str(document["canonical_key"])
        row = by_key.setdefault(key, {field: "" for field in TABLE_FIELDS})
        row["canonical_key"] = key
        row["family_key"] = document.get("family_key") or row.get("family_key") or ""
        row["document"] = document["document"]
        document_ids = _split_values(row.get("document_ids"))
        if document.get("document_id"):
            document_ids.add(str(document["document_id"]))
        row["document_ids"] = _join_values(document_ids)
        if document["missing"]:
            if row.get("last_potential_answer_id") == observation["answer_id"]:
                duplicates += 1
            else:
                increments += 1
                row["potential_count"] = _int(row.get("potential_count")) + 1
                row["first_needed_at"] = row.get("first_needed_at") or recorded_at
                row["last_needed_at"] = recorded_at
                row["last_potential_answer_id"] = observation["answer_id"]
        else:
            if row.get("last_used_answer_id") == observation["answer_id"]:
                duplicates += 1
            else:
                increments += 1
                row["used_count"] = _int(row.get("used_count")) + 1
                row["first_used_at"] = row.get("first_used_at") or recorded_at
                row["last_used_at"] = recorded_at
                row["last_used_answer_id"] = observation["answer_id"]

    refreshed = _refresh_rows(list(by_key.values()), kb_root)
    _write_table(table_path, refreshed)
    statistics = document_usage_statistics(state_root, kb_root)
    return {
        "answer_id": observation["answer_id"],
        "counter_increments": increments,
        "technical_duplicates_ignored": duplicates,
        "tracked_document_count": statistics["document_count"],
        "active_demand_count": statistics["active_demand_count"],
        "table_path": str(table_path),
    }
