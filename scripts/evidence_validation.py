"""Check source identity and quotation integrity; never equate a citation with truth."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from index_store import index_path, index_snapshot
from retrieval import fetch_document, current_structure, read_yaml


CLAUSE_LOCATOR = re.compile(r"^(\d+(?:\.\d+){0,7})(?:@(line:\d+))?$")


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def answer_digest(answer: str) -> str:
    return hashlib.sha256(answer.encode("utf-8")).hexdigest()


def evidence_digest(evidence: list[dict]) -> str:
    return hashlib.sha256(json.dumps(evidence, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _safe_normalized_path(root: Path, relative: Any) -> Path | None:
    if not isinstance(relative, str) or not relative:
        return None
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _locator_ranges(root: Path, document: Mapping[str, Any], clause: Any, page: Any) -> tuple[list[tuple[int, int]], str | None]:
    """Return the intersection of every declared locator, never their union."""
    source_path = _safe_normalized_path(root, document.get("normalized_path"))
    if source_path is None:
        return [], "normalized_text_unavailable"
    structural = current_structure(root, source_path)
    ranges: list[tuple[int, int]] = []
    if clause is not None:
        match = CLAUSE_LOCATOR.fullmatch(str(clause).strip())
        if not match:
            return [], "invalid_clause_locator"
        clause_id, anchor = match.groups()
        candidates = [entry for entry in structural.get("clauses", []) if entry.get("clause") == clause_id]
        if anchor is not None:
            candidates = [entry for entry in candidates if entry.get("anchor") == anchor]
        if len(candidates) != 1:
            return [], "unavailable_or_ambiguous_locator"
        ranges.append((int(candidates[0]["start_line"]), int(candidates[0]["end_line"])))
    if page is not None:
        try:
            page_number = int(page)
        except (TypeError, ValueError):
            return [], "invalid_page_locator"
        candidates = [entry for entry in structural.get("pages", []) if entry.get("page") == page_number]
        if len(candidates) != 1:
            return [], "unavailable_or_ambiguous_locator"
        ranges.append((int(candidates[0]["start_line"]), int(candidates[0]["end_line"])))
    if not ranges:
        return [], "missing_locator"
    start, end = max(value[0] for value in ranges), min(value[1] for value in ranges)
    return ([(start, end)] if start <= end else []), None


def _quote_in_locator_intersection(root: Path, document: Mapping[str, Any], clause: Any, page: Any, quote: str) -> tuple[bool, str | None]:
    ranges, error = _locator_ranges(root, document, clause, page)
    if error:
        return False, error
    if not ranges:
        return False, "locator_intersection_empty"
    source_path = _safe_normalized_path(root, document.get("normalized_path"))
    if source_path is None:  # Kept for type narrowing and a fail-closed race check.
        return False, "normalized_text_unavailable"
    lines = source_path.read_text(encoding="utf-8", errors="replace").splitlines()
    return any(quote in _text("\n".join(lines[start - 1:end])) for start, end in ranges), None


@index_snapshot
def validate_evidence(root: Path, answer: str, contract: dict[str, Any]) -> dict[str, Any]:
    documents = {str(item["id"]): item for item in read_yaml(index_path(root)).get("documents", []) if isinstance(item, dict) and item.get("id")}
    evidence = contract.get("evidence", [])
    if not isinstance(evidence, list) or any(not isinstance(item, dict) for item in evidence):
        raise ValueError("evidence must be an array of objects")
    declared_ids = contract.get("evidence_ids", [])
    if not isinstance(declared_ids, list):
        raise ValueError("evidence_ids must be an array")
    ids = {str(value) for value in declared_ids}
    ids.update(str(item.get("document_id") or "") for item in evidence)
    errors: list[str] = []
    mode = contract.get("evidence_mode", "passage" if ids else "none")
    if mode not in {"none", "metadata", "passage"}:
        raise ValueError("evidence_mode must be none, metadata or passage")
    if mode == "none" and ids:
        errors.append("evidence_cannot_be_disabled_with_citations")
    for doc_id in sorted(ids):
        if not doc_id or doc_id not in documents:
            errors.append(f"unknown_document:{doc_id}")
        if doc_id and doc_id not in answer:
            errors.append(f"uncited_document:{doc_id}")
        if mode == "passage" and doc_id and not any(item.get("document_id") == doc_id for item in evidence):
            errors.append(f"missing_passage:{doc_id}")
    for item in evidence:
        doc_id = str(item.get("document_id") or "")
        document = documents.get(doc_id)
        if not document:
            continue
        if mode == "metadata":
            expected_hash = item.get("source_sha256")
            actual_hash = document.get("source_sha256")
            if expected_hash is not None and str(expected_hash) != str(actual_hash):
                errors.append(f"source_changed:{doc_id}")
            continue
        if mode != "passage":
            continue
        clause = item.get("clause")
        page = item.get("page")
        quote = _text(item.get("quote"))
        if clause is None and page is None:
            errors.append(f"missing_locator:{doc_id}")
            continue
        if not quote:
            errors.append(f"missing_quote:{doc_id}")
            continue
        # The retrieval API resolves document identity and exposes its current
        # normalized hash. Locator intersection below then prevents a quote in
        # only a clause *or* only a page from being accepted as both.
        located = fetch_document(root, doc_id, [str(clause)] if clause is not None else [],
                                 [str(page)] if page is not None else [], None, max_chars=50000)
        if not located.get("complete") or not located.get("excerpts"):
            errors.append(f"unavailable_or_ambiguous_locator:{doc_id}")
            continue
        quote_matches, locator_error = _quote_in_locator_intersection(root, document, clause, page, quote)
        if locator_error:
            errors.append(f"{locator_error}:{doc_id}")
        elif not quote_matches:
            errors.append(f"quote_not_in_source:{doc_id}")
        expected_hash = item.get("source_sha256")
        actual_hashes = {excerpt.get("source_sha256") for excerpt in located["excerpts"]}
        if contract.get("require_semantic_review") and not expected_hash:
            errors.append(f"source_version_required:{doc_id}")
        if expected_hash and expected_hash not in actual_hashes:
            errors.append(f"source_changed:{doc_id}")
        claim = _text(item.get("claim"))
        if claim and claim not in _text(answer):
            errors.append(f"claim_not_in_answer:{doc_id}")
    review = contract.get("semantic_review") or {}
    semantic_required = bool(contract.get("require_semantic_review"))
    review_status = "not_performed"
    if review:
        if not isinstance(review, dict):
            raise ValueError("semantic_review must be an object")
        tied = review.get("answer_sha256") == answer_digest(answer) and review.get("evidence_sha256") == evidence_digest(evidence)
        if not tied:
            errors.append("semantic_review_stale")
        if not review.get("reviewer") or review.get("supported") is not True:
            errors.append("semantic_review_not_supported")
        review_status = "reviewer_attested" if tied and review.get("supported") is True and review.get("reviewer") else "failed"
    elif semantic_required:
        errors.append("semantic_review_required")
    return {"passed": not errors, "failures": errors,
            "source_validation": "checked" if ids else "not_applicable", "semantic_validation": review_status,
            "answer_sha256": answer_digest(answer), "evidence_sha256": evidence_digest(evidence),
            "scope": "Checks source/quote integrity; entailment requires the declared reviewer, not string matching."}
