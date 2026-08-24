#!/usr/bin/env python3
"""Canonical reference resolution for the normative knowledge base."""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


SPACE = re.compile(r"\s+")
YEAR = re.compile(r"(?:19|20)\d{2}")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize(value: str) -> str:
    value = value.upper().replace("Ё", "Е").replace("–", "-").replace("—", "-")
    value = value.replace("№", " N ")
    return SPACE.sub(" ", value).strip()


def _year(text: str) -> str | None:
    match = YEAR.search(text)
    return match.group(0) if match else None


def canonical_identifier(label: str) -> tuple[str | None, str | None]:
    """Return an exact document key and a family key.

    Exact keys include a revision year where the designation contains one.
    Family keys intentionally omit that year and are used only when one loaded
    document is the unique member of the family.
    """
    text = normalize(label)

    # Acts are checked before embedded standards: an approval order is not the ГОСТ it mentions.
    act_patterns = [
        (r"ПРИКАЗ\s+РОССТАНДАРТА.*?\bN\s*(\d+)(?:-СТ)?", "order-rosstandart"),
        (r"ПРИКАЗ\s+РОСТЕХНАДЗОРА.*?\bN\s*(\d+)", "order-rostechnadzor"),
        (r"РЕШЕНИЕ\s+НАБЛЮДАТЕЛЬНОГО\s+СОВЕТА.*?\bN\s*(\d+)", "rosatom-board-decision"),
        (r"ПОСТАНОВЛЕНИЕ\s+ГОССТАНДАРТА.*?\bN\s*(\d+)", "gosstandart-resolution"),
    ]
    for pattern, prefix in act_patterns:
        if match := re.search(pattern, text):
            year = _year(text)
            family = f"{prefix}:{match.group(1)}"
            return (f"{family}:{year}" if year else family), family

    if match := re.search(r"(?:ПОСТАНОВЛЕНИЕ\s+ПРАВИТЕЛЬСТВА(?:\s+РОССИЙСКОЙ\s+ФЕДЕРАЦИИ|\s+РФ)?|ПП\s+РФ).*?\bN\s*(\d+)", text):
        family = f"pp-rf:{match.group(1)}"
        year = _year(text)
        return (f"{family}:{year}" if year else family), family

    if match := re.search(r"(?:ФЕДЕРАЛЬНЫЙ\s+ЗАКОН.*?)?\bN\s*(\d+)\s*-?\s*ФЗ\b", text):
        family = f"fz:{match.group(1)}"
        return family, family

    if match := re.search(r"\bГОСТ\s+Р\s+([0-9][0-9.]*)(?:\s*-\s*((?:19|20)?\d{2}))?", text):
        number, year = match.groups()
        family = f"gost-r:{number}"
        return (f"{family}:{year}" if year else family), family

    if match := re.search(r"\bГОСТ\s+(?!Р\b)([0-9][0-9.]*)(?:\s*-\s*((?:19|20)?\d{2}))?", text):
        number, year = match.groups()
        family = f"gost:{number}"
        return (f"{family}:{year}" if year else family), family

    if match := re.search(r"\bНП\s*-?\s*([0-9]{2,3}\s*-\s*[0-9]{2,4})", text):
        designation = re.sub(r"\s+", "", match.group(1))
        family = f"np:{designation}"
        return family, family

    if match := re.search(r"\bРБ\s*-?\s*([0-9]{2,3}\s*-\s*[0-9]{2,4})", text):
        designation = re.sub(r"\s+", "", match.group(1))
        family = f"rb:{designation}"
        return family, family

    if match := re.search(r"\bРД\s+([А-ЯA-Z0-9][А-ЯA-Z0-9.\-/ ]{4,})", text):
        designation = re.sub(r"\s+", "-", match.group(1).strip(" .,-"))
        return f"rd:{designation}", f"rd:{designation}"

    return None, None


def identifier_from_id(doc_id: str) -> tuple[str | None, str | None]:
    value = doc_id.lower()
    patterns = [
        (r"^gost-r-(.+)-(\d{2,4})$", "gost-r"),
        (r"^gost-(.+)-(\d{2,4})$", "gost"),
        (r"^(?:np|p)-(\d{2,3}-\d{2,4})$", "np"),
        (r"^pp-rf-(\d+)-(\d{4})$", "pp-rf"),
        (r"^fz-(\d+)-fz$", "fz"),
    ]
    for pattern, prefix in patterns:
        if match := re.match(pattern, value):
            if prefix in {"gost", "gost-r"}:
                family = f"{prefix}:{match.group(1)}"
                return f"{family}:{match.group(2)}", family
            if prefix == "pp-rf":
                family = f"pp-rf:{match.group(1)}"
                return f"{family}:{match.group(2)}", family
            family = f"{prefix}:{match.group(1)}"
            return family, family
    return None, None


def reference_role(reference: dict[str, Any]) -> str:
    label = normalize(str(reference.get("cited_as", "")))
    action = normalize(str(reference.get("action", "")))
    location = normalize(str(reference.get("location", reference.get("source_anchor", ""))))
    combined = f"{label} {action} {location}"
    if "БИБЛИОГРАФ" in combined or "СПРАВОЧН" in combined:
        return "informative"
    if "РЕШЕНИЕ НАБЛЮДАТЕЛЬНОГО СОВЕТА" in label:
        return "amendment_act"
    if "СОСТАВ ИЗМЕНЕНИ" in combined or "РЕШЕНИЕ ОБ ИЗМЕНЕНИ" in combined:
        return "amendment_act"
    if "ПРИКАЗ РОССТАНДАРТА" in label or "ПОСТАНОВЛЕНИЕ ГОССТАНДАРТА" in label:
        return "approval_act"
    if "ПРИКАЗ РОСТЕХНАДЗОРА" in label and ("УТВЕРЖД" in combined or "РЕКВИЗИТ" in combined):
        return "approval_act"
    exact, family = canonical_identifier(label)
    if family and family.startswith(("np:", "rb:")):
        return "nuclear_rule"
    if family and family.startswith(("gost:", "gost-r:", "rd:")):
        return "normative_standard"
    if family and family.startswith(("fz:", "pp-rf:")):
        return "governing_act"
    if any(token in label for token in (" ТУ ", " ОТТ ", " СТО ")):
        return "project_specific"
    return "other"


def queue_key(reference: dict[str, Any]) -> str:
    label = str(reference.get("cited_as", ""))
    exact, family = canonical_identifier(label)
    return family or exact or "text:" + normalize(label).lower()


def queue_score(roles: set[str], cited_count: int, statuses: set[str]) -> tuple[int, str]:
    role_scores = {
        "nuclear_rule": 70,
        "project_specific": 70,
        "governing_act": 50,
        "normative_standard": 45,
        "other": 25,
        "approval_act": 5,
        "amendment_act": 5,
        "informative": 0,
    }
    score = max((role_scores.get(role, 20) for role in roles), default=20)
    score += min(30, max(0, cited_count - 1) * 10)
    if "устарел_нет_замены" in statuses:
        score += 15
    score = min(score, 100)
    priority = "high" if score >= 70 else "medium" if score >= 35 else "low"
    return score, priority


def _front_matter(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    end = text.find("\n---\n", 4)
    if not text.startswith("---\n") or end < 0:
        raise ValueError(f"Invalid front matter: {path}")
    return yaml.safe_load(text[4:end]) or {}, text[end + 5:]


def _write_document(path: Path, metadata: dict[str, Any], body: str) -> None:
    text = "---\n" + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False, width=100) + "---\n" + body
    path.write_text(text, encoding="utf-8")


def _document_maps(root: Path) -> tuple[dict[str, str], dict[str, list[str]], set[str]]:
    exact: dict[str, str] = {}
    families: dict[str, list[str]] = defaultdict(list)
    ids: set[str] = set()
    for path in (root / "docs").rglob("*.md"):
        if "_templates" in path.parts:
            continue
        metadata, _ = _front_matter(path)
        doc_id = str(metadata.get("id"))
        ids.add(doc_id)
        keys = [identifier_from_id(doc_id)]
        for field in ("short_title", "title"):
            if metadata.get(field):
                keys.append(canonical_identifier(str(metadata[field])))
        for exact_key, family_key in keys:
            if exact_key:
                exact[exact_key] = doc_id
            if family_key and doc_id not in families[family_key]:
                families[family_key].append(doc_id)
    return exact, families, ids


def resolve_label(label: str, exact: dict[str, str], families: dict[str, list[str]]) -> tuple[str | None, str, str | None]:
    exact_key, family_key = canonical_identifier(label)
    if exact_key and exact_key in exact:
        return exact[exact_key], "canonical_exact", exact_key
    if family_key and len(families.get(family_key, [])) == 1:
        return families[family_key][0], "canonical_unique_family", family_key
    return None, "unresolved", exact_key or family_key


def synchronize_references(root: Path, write: bool = True) -> dict[str, Any]:
    exact, families, ids = _document_maps(root)
    changed_docs = 0
    resolved = 0
    repaired = 0
    ambiguous: list[dict[str, str]] = []
    for path in sorted((root / "docs").rglob("*.md")):
        if "_templates" in path.parts:
            continue
        metadata, body = _front_matter(path)
        changed = False
        for reference in metadata.get("references", []):
            label = str(reference.get("cited_as", ""))
            target, method, canonical = resolve_label(label, exact, families)
            role = reference_role(reference)
            if reference.get("reference_role") != role:
                reference["reference_role"] = role
                changed = True
            if canonical and reference.get("canonical_identifier") != canonical:
                reference["canonical_identifier"] = canonical
                changed = True
            current_target = reference.get("target_document")
            status = reference.get("status")
            if target:
                if status == "отсутствует":
                    reference["status"] = "в_базе"
                    reference["target_document"] = target
                    reference["resolution_method"] = method
                    reference["resolved_at"] = utc_now()
                    reference["confidence"] = "high" if method == "canonical_exact" else "medium"
                    reference["basis"] = f"Автоматически сопоставлено с карточкой {target} по каноническому обозначению."
                    reference.pop("action", None)
                    changed = True
                    resolved += 1
                elif status == "в_базе" and current_target != target:
                    reference["target_document"] = target
                    reference["resolution_method"] = method
                    reference["confidence"] = "high" if method == "canonical_exact" else "medium"
                    changed = True
                    repaired += 1
                elif status == "в_базе" and current_target == target and reference.get("confidence") in {None, "not_assessed"}:
                    reference["resolution_method"] = method
                    reference["confidence"] = "high" if method == "canonical_exact" else "medium"
                    changed = True
                elif status in {"устарел_есть_замена", "устарел_специфика"} and not current_target:
                    reference["target_document"] = target
                    reference["resolution_method"] = method
                    changed = True
            elif status == "в_базе" and current_target not in ids:
                reference["status"] = "отсутствует"
                reference.pop("target_document", None)
                reference["requires_analysis"] = True
                reference["confidence"] = "low"
                reference["action"] = "Повторно идентифицировать документ: указанная цель отсутствует в базе."
                changed = True
                repaired += 1
            if not reference.get("confidence"):
                reference["confidence"] = "not_assessed"
                changed = True
            if canonical and len(families.get(canonical, [])) > 1 and not target:
                ambiguous.append({"source": str(metadata.get("id")), "label": label, "family": canonical})
        if changed:
            changed_docs += 1
            if write:
                _write_document(path, metadata, body)
    return {
        "changed_documents": changed_docs,
        "resolved_references": resolved,
        "repaired_references": repaired,
        "ambiguous": ambiguous,
    }
