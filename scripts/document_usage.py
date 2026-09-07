#!/usr/bin/env python3
"""Maintain one simple table of used and potentially needed documents."""
from __future__ import annotations

import csv
import json
import os
import re
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

from reference_resolver import canonical_identifier, identifier_from_id, normalize
from index_store import index_path, pin_indexes


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
    # These are implementation-only idempotency sets.  They deliberately are
    # not exposed by document_usage_statistics().
    "used_answer_ids",
    "potential_answer_ids",
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
    """Read the one counter table without silently repairing bad data.

    Older versions did not keep all answer ids.  Such files are accepted and
    upgraded on the next write, but a broken counter table is an operational
    error: replacing it with an empty table would lose the usage history.
    """
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, delimiter=";")
        if not reader.fieldnames:
            raise ValueError(f"Таблица использования повреждена: нет заголовка ({path})")
        headers = {header.strip() for header in reader.fieldnames if header}
        if not {"document", "canonical_key"}.issubset(headers):
            raise ValueError(
                f"Таблица использования повреждена: нужны document и canonical_key ({path})"
            )
        rows = [dict(row) for row in reader]
    seen: set[str] = set()
    for number, row in enumerate(rows, 2):
        if None in row or not str(row.get("document") or "").strip():
            raise ValueError(f"Таблица использования повреждена в строке {number} ({path})")
        key = str(row.get("canonical_key") or "").strip()
        if not key:
            raise ValueError(f"Таблица использования повреждена: пустой canonical_key в строке {number} ({path})")
        if key in seen:
            raise ValueError(f"Таблица использования повреждена: повтор canonical_key {key!r} ({path})")
        seen.add(key)
        for counter in ("used_count", "potential_count"):
            value = str(row.get(counter) or "0").strip()
            try:
                parsed = int(value)
            except ValueError as error:
                raise ValueError(f"Таблица использования повреждена: {counter} в строке {number}") from error
            if parsed < 0:
                raise ValueError(f"Таблица использования повреждена: отрицательный {counter} в строке {number}")
    return rows


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
    loaded = yaml.load(path.read_text(encoding="utf-8"), Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader)) or {}
    return loaded if isinstance(loaded, dict) else {}


def _split_values(value: Any) -> set[str]:
    return {item.strip() for item in str(value or "").split("|") if item.strip()}


def _join_values(values: Iterable[str]) -> str:
    return " | ".join(sorted({str(value).strip() for value in values if str(value).strip()}))


def _answer_ids(value: Any, legacy: Any = "") -> set[str]:
    """Read a compact technical id list, retaining the old last-id column."""
    values: set[str] = set()
    raw = str(value or "").strip()
    if raw:
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError:
            # Early development files may have used a pipe-separated value.
            loaded = raw.split("|")
        if not isinstance(loaded, list) or not all(isinstance(item, str) for item in loaded):
            raise ValueError("Таблица использования повреждена: технический список answer_id")
        values.update(item.strip() for item in loaded if item.strip())
    if str(legacy or "").strip():
        values.add(str(legacy).strip())
    return values


def _write_answer_ids(values: Iterable[str]) -> str:
    return json.dumps(sorted({value for value in values if value}), ensure_ascii=False, separators=(",", ":"))


def _loaded_document_maps(
    kb_root: Path,
) -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, str], dict[str, str]]:
    """Build aliases for real loaded records, without treating editions alike."""
    exact: dict[str, set[str]] = defaultdict(set)
    families: dict[str, set[str]] = defaultdict(set)
    primary_exact: dict[str, str] = {}
    primary_family: dict[str, str] = {}
    # Index publication may switch the active generation.  Pin it while
    # resolving aliases so a refresh cannot mix records from two generations.
    with pin_indexes(kb_root):
        documents = _read_yaml(index_path(kb_root, "meta/documents.yaml")).get("documents", [])
    for item in documents:
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
                # Prefer an explicit canonical_exact but never invent an
                # edition from a family-only designation.
                primary_exact.setdefault(document_id, str(exact_key))
            if family_key:
                families[str(family_key)].add(document_id)
                primary_family.setdefault(document_id, str(family_key))
    return exact, families, primary_exact, primary_family


def _identity_candidates(label: str, document_id: str = "") -> tuple[set[str], set[str]]:
    """Return exact/family aliases for a label, including machine-style ids."""
    exact: set[str] = set()
    families: set[str] = set()
    for value in (label, document_id):
        value = str(value or "").strip()
        if not value:
            continue
        canonical, family = canonical_identifier(value)
        id_canonical, id_family = identifier_from_id(value.lower())
        if canonical:
            exact.add(str(canonical))
        if id_canonical:
            exact.add(str(id_canonical))
        if family:
            families.add(str(family))
        if id_family:
            families.add(str(id_family))
        if document_id and value == document_id:
            exact.add(f"id:{value.lower()}")
    return exact, families


def _resolve_identity(
    label: str,
    document_id: str,
    loaded_exact: dict[str, set[str]],
    loaded_families: dict[str, set[str]],
    primary_exact: dict[str, str],
) -> tuple[str, str | None, set[str]]:
    """Use an exact loaded target, or one uniquely loaded family.

    Explicit editions must match exactly.  Family matching is allowed only for
    an unqualified observation and only where the corpus contains one edition.
    """
    exact_candidates, families = _identity_candidates(label, document_id)
    ids: set[str] = set()
    for candidate in exact_candidates:
        ids.update(loaded_exact.get(candidate, set()))
    if len(ids) == 1:
        document = next(iter(ids))
        return primary_exact.get(document, next(iter(exact_candidates))), next(iter(families), None), ids

    canonical, family = document_key(label, document_id)
    # A family key as the exact key means the caller did not identify an
    # edition.  Do not apply this shortcut to explicit GOST/PP years.
    unqualified = bool(family and canonical == family)
    family_ids: set[str] = set()
    if unqualified:
        for candidate in families:
            family_ids.update(loaded_families.get(candidate, set()))
    if len(family_ids) == 1:
        document = next(iter(family_ids))
        return primary_exact.get(document, canonical), family or next(iter(families), None), family_ids
    return canonical, family, set()


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _counter_ids(row: dict[str, Any], kind: str) -> set[str]:
    return _answer_ids(row.get(f"{kind}_answer_ids"), row.get(f"last_{kind}_answer_id"))


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    for counter in ("used_count", "potential_count"):
        row[counter] = _int(row.get(counter))
    for kind in ("used", "potential"):
        ids = _counter_ids(row, kind)
        row[f"{kind}_answer_ids"] = _write_answer_ids(ids)
        if ids:
            last = str(row.get(f"last_{kind}_answer_id") or "").strip()
            row[f"last_{kind}_answer_id"] = last if last in ids else sorted(ids)[-1]
    return row


def _merge_counter_rows(target: dict[str, Any], incoming: dict[str, Any], kind: str) -> None:
    """Merge an alias migration without counting a retry twice.

    New rows are exactly answer-id based.  Legacy files know only a count and
    perhaps a final id: preserve those historical totals, while subtracting
    only id overlap that can actually be proven.
    """
    target_ids = _counter_ids(target, kind)
    incoming_ids = _counter_ids(incoming, kind)
    merged_ids = target_ids | incoming_ids
    target_unknown = max(0, _int(target.get(f"{kind}_count")) - len(target_ids))
    incoming_unknown = max(0, _int(incoming.get(f"{kind}_count")) - len(incoming_ids))
    target[f"{kind}_count"] = target_unknown + incoming_unknown + len(merged_ids)
    target[f"{kind}_answer_ids"] = _write_answer_ids(merged_ids)
    if merged_ids:
        current_last = str(target.get(f"last_{kind}_answer_id") or "").strip()
        incoming_last = str(incoming.get(f"last_{kind}_answer_id") or "").strip()
        target[f"last_{kind}_answer_id"] = (
            current_last if current_last in merged_ids
            else incoming_last if incoming_last in merged_ids
            else sorted(merged_ids)[-1]
        )
    first = f"first_{'used' if kind == 'used' else 'needed'}_at"
    last = f"last_{'used' if kind == 'used' else 'needed'}_at"
    dates = [value for value in (target.get(first), incoming.get(first)) if value]
    if dates:
        target[first] = min(dates)
    dates = [value for value in (target.get(last), incoming.get(last)) if value]
    if dates:
        target[last] = max(dates)


def _refresh_rows(rows: list[dict[str, str]], kb_root: Path) -> list[dict[str, Any]]:
    loaded_exact, loaded_families, primary_exact, _primary_family = _loaded_document_maps(kb_root)
    loaded_ids = {document_id for ids in loaded_exact.values() for document_id in ids}
    refreshed_by_key: dict[str, dict[str, Any]] = {}
    for source in rows:
        row: dict[str, Any] = {field: source.get(field, "") for field in TABLE_FIELDS}
        key, family_key, resolved_ids = _resolve_identity(
            str(row.get("document") or ""),
            "",
            loaded_exact,
            loaded_families,
            primary_exact,
        )
        # The stored canonical key is a more reliable alias than a display
        # label, especially for rows created before an upload.
        stored_key = str(row.get("canonical_key") or "")
        stored_family = str(row.get("family_key") or "")
        stored_ids: set[str] = set()
        for candidate in (stored_key, stored_family):
            stored_ids.update(loaded_exact.get(candidate, set()))
        if len(stored_ids) == 1:
            resolved = next(iter(stored_ids))
            key = primary_exact.get(resolved, key)
            resolved_ids.add(resolved)
        elif stored_key and not resolved_ids:
            # Do not replace a known explicit edition with a looser label.
            key = stored_key
            family_key = stored_family or family_key
        # document_ids denotes currently loaded records, not a sticky history
        # of former uploads.  Counts remain untouched when a file is removed.
        document_ids = _split_values(row.get("document_ids")) & loaded_ids
        document_ids.update(resolved_ids)
        document_ids.update(loaded_exact.get(key, set()))
        if not document_ids and family_key and key == family_key:
            family_ids = loaded_families.get(family_key, set())
            if len(family_ids) == 1:
                document_ids.update(family_ids)
        row["canonical_key"] = key
        row["family_key"] = family_key or ""
        row["document_ids"] = _join_values(document_ids)
        _normalize_row(row)
        existing = refreshed_by_key.get(key)
        if existing is None:
            refreshed_by_key[key] = row
        else:
            # A family-only historical row may become the same unique loaded
            # edition as a later row.  This is a controlled migration, unlike
            # accepting duplicate source canonical keys in _read_table().
            _merge_counter_rows(existing, row, "used")
            _merge_counter_rows(existing, row, "potential")
            existing["document_ids"] = _join_values(
                _split_values(existing.get("document_ids")) | document_ids
            )
            if not existing.get("document"):
                existing["document"] = row.get("document")

    refreshed = list(refreshed_by_key.values())
    for row in refreshed:
        row["status"] = "loaded" if _split_values(row.get("document_ids")) else "needed" if row["potential_count"] else "observed"

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


def refresh_document_usage(state_root: Path, kb_root: Path) -> dict[str, Any]:
    """Persist corpus-dependent statuses and aliases without recording usage."""
    table_path = state_root / TABLE_PATH
    rows = _refresh_rows(_read_table(table_path), kb_root)
    if table_path.is_file():
        _write_table(table_path, rows)
    return {
        "tracked_document_count": len(rows),
        "refreshed_at": utc_now(),
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
    loaded_exact, loaded_families, primary_exact, _primary_family = _loaded_document_maps(kb_root)
    recorded_at = utc_now()
    increments = 0
    duplicates = 0
    for document in observation["documents"]:
        key, family_key, resolved_ids = _resolve_identity(
            str(document["document"]),
            str(document.get("document_id") or ""),
            loaded_exact,
            loaded_families,
            primary_exact,
        )
        row = by_key.setdefault(key, {field: "" for field in TABLE_FIELDS})
        row["canonical_key"] = key
        row["family_key"] = family_key or document.get("family_key") or row.get("family_key") or ""
        row["document"] = document["document"]
        document_ids = _split_values(row.get("document_ids"))
        document_ids.update(resolved_ids)
        if document.get("document_id"):
            document_ids.add(str(document["document_id"]))
        row["document_ids"] = _join_values(document_ids)
        if document["missing"]:
            answer_ids = _counter_ids(row, "potential")
            if observation["answer_id"] in answer_ids:
                duplicates += 1
            else:
                increments += 1
                row["potential_count"] = _int(row.get("potential_count")) + 1
                row["first_needed_at"] = row.get("first_needed_at") or recorded_at
                row["last_needed_at"] = recorded_at
                row["last_potential_answer_id"] = observation["answer_id"]
                row["potential_answer_ids"] = _write_answer_ids(answer_ids | {observation["answer_id"]})
        else:
            answer_ids = _counter_ids(row, "used")
            if observation["answer_id"] in answer_ids:
                duplicates += 1
            else:
                increments += 1
                row["used_count"] = _int(row.get("used_count")) + 1
                row["first_used_at"] = row.get("first_used_at") or recorded_at
                row["last_used_at"] = recorded_at
                row["last_used_answer_id"] = observation["answer_id"]
                row["used_answer_ids"] = _write_answer_ids(answer_ids | {observation["answer_id"]})

    refreshed = _refresh_rows(list(by_key.values()), kb_root)
    _write_table(table_path, refreshed)
    statistics = document_usage_statistics(state_root, kb_root)
    return {
        "answer_id": observation["answer_id"],
        "recorded_at": recorded_at,
        "counter_increments": increments,
        "technical_duplicates_ignored": duplicates,
        "tracked_document_count": statistics["document_count"],
        "active_demand_count": statistics["active_demand_count"],
        "table_path": str(table_path),
    }
