#!/usr/bin/env python3
"""Bounded retrieval and structural indexes for the normative knowledge base."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

import yaml

from reference_resolver import canonical_identifier
from index_store import index_path, index_snapshot


PAGE_MARKER = re.compile(r"^\s*=+\s*СТРАНИЦА\s+(\d+)\s*=+\s*$", re.IGNORECASE)
CLAUSE_HEADING = re.compile(r"^\s*(\d+(?:\.\d+){0,7})[.)]?\s+(.{1,220}?)\s*$")
DATE_HEADING = re.compile(r"^\s*\d{1,2}\.\d{1,2}\.\d{4}(?:\s|$)")
HEADER_NOISE = re.compile(r"(?:СИСТЕМА\s+ГАРАНТ|WWW\.GARANT|КОНСУЛЬТАНТ\+)", re.IGNORECASE)
WORD = re.compile(r"[0-9a-zа-яё]+", re.IGNORECASE)
SPACE = re.compile(r"\s+")
SEARCH_STOPWORDS = {
    "без", "был", "была", "были", "для", "его", "ее", "или", "из", "их", "как", "на", "от",
    "по", "при", "со", "также", "это",
}
INDEX_SCHEMA_VERSION = 2
_DOCUMENT_CACHE: tuple[str, tuple[int, int], dict[str, dict[str, Any]]] | None = None
_JSON_CACHE: dict[str, tuple[tuple[int, int], dict[str, Any]]] = {}
_SOURCE_HASH_CACHE: dict[str, tuple[tuple[int, int], str]] = {}


def read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    # CSafeLoader is materially cheaper for the registry on installations that ship it.
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    loaded = yaml.load(path.read_text(encoding="utf-8"), Loader=loader)
    return loaded if isinstance(loaded, dict) else {}


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    stat_key = (path.stat().st_mtime_ns, path.stat().st_size)
    cached = _JSON_CACHE.get(str(path.resolve()))
    if cached and cached[0] == stat_key:
        return cached[1]
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(loaded, dict):
        _JSON_CACHE[str(path.resolve())] = (stat_key, loaded)
    return loaded if isinstance(loaded, dict) else {}


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _JSON_CACHE.pop(str(path.resolve()), None)


def compact_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)


def print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def normalized(value: Any) -> str:
    text = str(value or "").lower().replace("ё", "е").replace("–", "-").replace("—", "-")
    return SPACE.sub(" ", text).strip()


def tokens(value: Any) -> list[str]:
    return [item.replace("ё", "е").lower() for item in WORD.findall(str(value or "")) if len(item) > 1]


def unique_strings(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        key = normalized(item)
        if item and key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _term_matches(term: str, candidate: str) -> bool:
    if term == candidate:
        return True
    # A small deterministic common-stem allowance covers common Russian case endings.
    common = 0
    for left, right in zip(term, candidate):
        if left != right:
            break
        common += 1
    return common >= max(5, min(len(term), len(candidate)) - 3)


def _matched_terms(query_terms: list[str], field_tokens: set[str]) -> list[str]:
    return [term for term in query_terms if term in field_tokens or any(_term_matches(term, candidate) for candidate in field_tokens)]


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _sha256(path: Path) -> str:
    resolved = str(path.resolve())
    stat_key = (path.stat().st_mtime_ns, path.stat().st_size)
    cached = _SOURCE_HASH_CACHE.get(resolved)
    if cached and cached[0] == stat_key:
        return cached[1]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    _SOURCE_HASH_CACHE[resolved] = (stat_key, digest)
    return digest


def _line_offsets(lines: list[str]) -> list[int]:
    offsets: list[int] = []
    position = 0
    for line in lines:
        offsets.append(position)
        position += len(line)
    offsets.append(position)
    return offsets


def _page_for_line(pages: list[dict[str, Any]], line_number: int) -> int | None:
    for page in pages:
        if page["start_line"] <= line_number <= page["end_line"]:
            return page["page"]
    return None


def index_normalized_document(root: Path, path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    offsets = _line_offsets(lines)
    page_markers: list[tuple[int, int]] = []
    headings: list[dict[str, Any]] = []

    for index, line in enumerate(lines):
        stripped = line.rstrip("\r\n")
        if page := PAGE_MARKER.match(stripped):
            page_markers.append((index, int(page.group(1))))
            continue
        if DATE_HEADING.match(stripped) or HEADER_NOISE.search(stripped):
            continue
        if len(stripped) <= 240 and (heading := CLAUSE_HEADING.match(stripped)):
            clause, title = heading.groups()
            # Reject ordinary list items whose "title" is only punctuation or a number.
            if any(character.isalpha() for character in title):
                headings.append({
                    "clause": clause.rstrip("."),
                    "title": title.strip(),
                    "depth": clause.count(".") + 1,
                    "line_index": index,
                    "anchor": f"line:{index + 1}",
                })

    pages: list[dict[str, Any]] = []
    if page_markers:
        for marker_index, (line_index, page_number) in enumerate(page_markers):
            start_index = line_index + 1
            end_index = page_markers[marker_index + 1][0] if marker_index + 1 < len(page_markers) else len(lines)
            pages.append({
                "page": page_number,
                "start_line": start_index + 1,
                "end_line": max(start_index + 1, end_index),
                "start_char": offsets[start_index],
                "end_char": offsets[end_index],
            })

    clauses: list[dict[str, Any]] = []
    for index, heading in enumerate(headings):
        end_index = len(lines)
        for following in headings[index + 1:]:
            if following["depth"] <= heading["depth"]:
                end_index = following["line_index"]
                break
        start_line = heading["line_index"] + 1
        end_line = max(start_line, end_index)
        clauses.append({
            "clause": heading["clause"],
            "title": heading["title"],
            "anchor": heading["anchor"],
            "depth": heading["depth"],
            "page": _page_for_line(pages, start_line),
            "end_page": _page_for_line(pages, end_line),
            "start_line": start_line,
            "end_line": end_line,
            "start_char": offsets[heading["line_index"]],
            "end_char": offsets[end_index],
        })

    return {
        "path": _relative(root, path),
        "sha256": _sha256(path),
        "characters": len(text),
        "lines": len(lines),
        "pages": pages,
        "clauses": clauses,
        "source_page_markers": bool(page_markers),
        "source_quality": "page_markers_present" if page_markers else "page_markers_absent",
    }


def build_retrieval_indexes(
    root: Path, documents: list[dict[str, Any]], indexed_at: str, output_meta: Path | None = None,
) -> dict[str, int]:
    """Build indexes, optionally into an unpublished generation directory."""
    previous = read_json(index_path(root, "meta/clause-index.json"))
    previous_documents = previous.get("documents", {}) if previous.get("schema_version") == INDEX_SCHEMA_VERSION else {}
    previous_search = read_json(index_path(root, "meta/search-index.json"))
    previous_search_entries = {
        str(item.get("id")): item for item in previous_search.get("documents", [])
        if isinstance(item, dict) and item.get("id")
    } if previous_search.get("schema_version") == INDEX_SCHEMA_VERSION else {}
    structural_documents: dict[str, dict[str, Any]] = {}
    searchable_documents: list[dict[str, Any]] = []
    clause_count = 0
    page_count = 0
    reused_documents = 0
    rebuilt_documents = 0

    for document in documents:
        doc_id = str(document["id"])
        normalized_path = document.get("normalized_path")
        structural: dict[str, Any] | None = None
        content_terms: list[str] = []
        if normalized_path:
            candidate = (root / str(normalized_path)).resolve()
            if root.resolve() in candidate.parents and candidate.is_file():
                digest = _sha256(candidate)
                cached = previous_documents.get(doc_id)
                relative_path = _relative(root, candidate)
                cached_search = previous_search_entries.get(doc_id, {})
                cached_content = cached_search.get("fields", {}).get("content")
                can_reuse = (
                    isinstance(cached, dict) and cached.get("sha256") == digest
                    and cached.get("path") == relative_path
                    and isinstance(cached_content, list)
                )
                if can_reuse:
                    structural = cached
                    content_terms = unique_strings(cached_content)
                    reused_documents += 1
                else:
                    structural = index_normalized_document(root, candidate)
                    content_terms = sorted(set(tokens(candidate.read_text(encoding="utf-8", errors="replace"))))
                    rebuilt_documents += 1
                structural_documents[doc_id] = structural
                clause_count += len(structural["clauses"])
                page_count += len(structural["pages"])

        fields = {
            "id": [doc_id],
            "designation": [document.get("short_title"), document.get("canonical_exact"), document.get("canonical_family")],
            "title": [document.get("title")],
            "category": document.get("categories", []),
            "applies_to": document.get("applies_to", []),
            "issuer": [document.get("issuer")],
            "clause": [
                f"{item.get('clause')} {item.get('title')}"
                for item in (structural or {}).get("clauses", [])
            ],
            "content": content_terms,
        }
        searchable_documents.append({
            "id": doc_id,
            "fields": {name: unique_strings(values) for name, values in fields.items()},
        })

    destination = output_meta or root / "meta"
    write_json(destination / "clause-index.json", {
        "schema_version": INDEX_SCHEMA_VERSION,
        "indexed_at": indexed_at,
        "documents": structural_documents,
    })
    write_json(destination / "search-index.json", {
        "schema_version": INDEX_SCHEMA_VERSION,
        "indexed_at": indexed_at,
        "documents": searchable_documents,
    })
    return {
        "indexed_documents": len(structural_documents), "clauses": clause_count, "pages": page_count,
        "reused_documents": reused_documents, "rebuilt_documents": rebuilt_documents,
    }


def current_structure(root: Path, source_path: Path) -> dict[str, Any]:
    """Return the current structural index for a normalized file, rebuilding only when stale."""
    source_path = source_path.resolve()
    root_path = root.resolve()
    if root_path not in source_path.parents or not source_path.is_file():
        raise ValueError("Нормализованный файл должен находиться внутри базы")
    relative_path = _relative(root_path, source_path)
    digest = _sha256(source_path)
    clause_index = read_json(index_path(root, "meta/clause-index.json"))
    if clause_index.get("schema_version") == INDEX_SCHEMA_VERSION:
        for structural in clause_index.get("documents", {}).values():
            if (isinstance(structural, dict) and structural.get("path") == relative_path
                    and structural.get("sha256") == digest):
                return structural
    return index_normalized_document(root, source_path)


def _documents_by_id(root: Path) -> dict[str, dict[str, Any]]:
    global _DOCUMENT_CACHE
    registry = index_path(root, "meta/documents.yaml")
    if not registry.exists():
        return {}
    key = (registry.stat().st_mtime_ns, registry.stat().st_size)
    cache_path = str(registry.resolve())
    if _DOCUMENT_CACHE and _DOCUMENT_CACHE[0] == cache_path and _DOCUMENT_CACHE[1] == key:
        return _DOCUMENT_CACHE[2]
    result = {
        str(item.get("id")): item
        for item in read_yaml(registry).get("documents", [])
        if item.get("id")
    }
    _DOCUMENT_CACHE = (cache_path, key, result)
    return result


def _search_entries(root: Path) -> list[dict[str, Any]]:
    data = read_json(index_path(root, "meta/search-index.json"))
    entries = data.get("documents", [])
    return entries if isinstance(entries, list) else []


@index_snapshot
def search_documents(root: Path, query: str, limit: int = 6, max_chars: int = 12000) -> dict[str, Any]:
    query = str(query).strip()[:1000]
    query_terms = [term for term in unique_strings(tokens(query)) if term not in SEARCH_STOPWORDS]
    query_normalized = normalized(query)
    query_exact, query_family = canonical_identifier(query)
    documents = _documents_by_id(root)
    weights = {
        "id": 18, "designation": 16, "title": 8, "clause": 6,
        "category": 5, "applies_to": 4, "issuer": 2, "content": 1,
    }
    ranked: list[dict[str, Any]] = []
    exact_documents = [document for document in documents.values()
                       if query_normalized in {normalized(document.get("id")), normalized(document.get("short_title"))}]
    # A full ID/designation needs only registry metadata, not the large content index.
    # Mixed/topic queries still search the entire corpus.
    entries = [{"id": document["id"], "fields": {
        "id": [document["id"]], "designation": [document.get("short_title")], "title": [document.get("title")]
    }} for document in exact_documents] if exact_documents and query_normalized else _search_entries(root)
    for entry in entries:
        doc_id = str(entry.get("id", ""))
        document = documents.get(doc_id)
        if not document:
            continue
        score = 0
        matched_fields: list[str] = []
        matched: set[str] = set()
        for field_name, values in entry.get("fields", {}).items():
            field_text = " ".join(values) if field_name == "content" else normalized(" ".join(str(value) for value in values))
            field_tokens = set(values) if field_name == "content" else set(tokens(field_text))
            field_matches = _matched_terms(query_terms, field_tokens)
            if query_normalized and query_normalized in field_text:
                score += weights.get(field_name, 1) * 2
            if field_matches:
                score += weights.get(field_name, 1) * len(field_matches)
                if len(field_matches) == len(query_terms):
                    score += weights.get(field_name, 1) * len(field_matches)
                matched_fields.append(field_name)
                matched.update(field_matches)
        if query_exact and query_exact == document.get("canonical_exact"):
            score += 120
            matched_fields.append("canonical_exact")
        elif query_family and query_family == document.get("canonical_family"):
            score += 70
            matched_fields.append("canonical_family")
        if query_normalized == normalized(doc_id) or query_normalized == normalized(document.get("short_title")):
            score += 150
        if score <= 0:
            continue
        if document.get("lifecycle_stage") == "approved_for_operational_use":
            score += 4
        ranked.append({
            "id": doc_id,
            "designation": document.get("short_title"),
            "title": document.get("title"),
            "status": document.get("status"),
            "lifecycle_stage": document.get("lifecycle_stage"),
            "categories": document.get("categories", []),
            "applies_to": document.get("applies_to", []),
            "path": document.get("path"),
            "normalized_path": document.get("normalized_path"),
            "source_pages": document.get("source_pages"),
            "extraction_confidence": document.get("extraction_confidence"),
            "replaces": document.get("replaces", []),
            "replaced_by": document.get("replaced_by", []),
            "score": score,
            "matched_fields": unique_strings(matched_fields),
            "matched_terms": sorted(matched),
        })

    ranked.sort(key=lambda item: (-item["score"], item["id"]))
    requested_limit = max(1, min(int(limit), 20))
    selected = ranked[:requested_limit]
    manifest = read_yaml(index_path(root, "meta/corpus-manifest.yaml"))
    result: dict[str, Any] = {
        "query": query,
        "strategy": "exact_metadata" if exact_documents else "ranked_search",
        "results": selected,
        "result_count": len(selected),
        "available_matches": len(ranked),
        "corpus_cutoff": manifest.get("last_indexed_at"),
        "truncated": len(ranked) > len(selected),
    }
    budget = max(1000, min(int(max_chars), 50000))
    while result["results"] and len(compact_json(result)) > budget:
        result["results"].pop()
        result["result_count"] = len(result["results"])
        result["truncated"] = True
    return result


def _parse_selectors(values: Iterable[str] | None) -> list[str]:
    return unique_strings(part for value in (values or []) for part in str(value).split(","))


CLAUSE_SELECTOR = re.compile(r"^(\d+(?:\.\d+){0,7})(?:@(line:\d+))?$")


def _parse_clause_selector(selector: str) -> tuple[str, str | None] | None:
    """Parse a bare clause or a disambiguating structural anchor."""
    match = CLAUSE_SELECTOR.fullmatch(selector.strip())
    return match.groups() if match else None


def _parse_pages(values: Iterable[str] | None) -> list[int]:
    pages: set[int] = set()
    for selector in _parse_selectors(values):
        if "-" in selector:
            start_text, end_text = selector.split("-", 1)
            start, end = int(start_text), int(end_text)
            if end < start or end - start > 100:
                raise ValueError(f"Некорректный диапазон страниц: {selector}")
            pages.update(range(start, end + 1))
        else:
            pages.add(int(selector))
    return sorted(pages)


def _resolve_document(root: Path, selector: str) -> tuple[dict[str, Any] | None, list[str]]:
    documents = _documents_by_id(root)
    if selector in documents:
        return documents[selector], []
    exact, family = canonical_identifier(selector)
    # Exact designation lookup deliberately precedes fuzzy search; it avoids a full
    # index scan and cannot substitute another edition.
    if exact:
        exact_matches = [item for item in documents.values() if item.get("canonical_exact") == exact]
        if len(exact_matches) == 1:
            return exact_matches[0], [str(exact_matches[0]["id"])]
        if family and exact != family:
            # A missing stated edition must remain missing even when a fuzzy search
            # would find a different edition in the same family.
            alternatives = [str(item["id"]) for item in documents.values()
                            if item.get("canonical_family") == family]
            return None, alternatives
    result = search_documents(root, selector, limit=3, max_chars=8000)
    matches = [item["id"] for item in result["results"]]
    if len(matches) == 1 or (len(matches) > 1 and result["results"][0]["score"] > result["results"][1]["score"] * 1.5):
        return documents[matches[0]], matches
    return None, matches


def _merge_ranges(ranges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    # Merge in textual order so overlapping ranges never lose their left edge.
    for item in sorted(ranges, key=lambda value: (value["start_char"], value["end_char"])):
        if merged and item["start_char"] <= merged[-1]["end_char"]:
            merged[-1]["start_char"] = min(merged[-1]["start_char"], item["start_char"])
            merged[-1]["end_char"] = max(merged[-1]["end_char"], item["end_char"])
            merged[-1]["start_line"] = min(merged[-1]["start_line"], item["start_line"])
            merged[-1]["end_line"] = max(merged[-1]["end_line"], item["end_line"])
            merged[-1]["_rank"] = max(int(merged[-1].get("_rank", 0)), int(item.get("_rank", 0)))
            merged[-1]["selectors"] = unique_strings([*merged[-1]["selectors"], *item["selectors"]])
        else:
            merged.append(dict(item))
    return sorted(merged, key=lambda value: (-int(value.get("_rank", 0)), value["start_char"]))


def _query_ranges(text: str, query: str, context_lines: int, limit: int = 8) -> list[dict[str, Any]]:
    query_terms = [term for term in unique_strings(tokens(query)) if term not in SEARCH_STOPWORDS]
    if not query_terms:
        return []
    lines = text.splitlines(keepends=True)
    offsets = _line_offsets(lines)
    ranges: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        line_tokens = set(tokens(line))
        matched = _matched_terms(query_terms, line_tokens)
        if not matched:
            continue
        start = max(0, index - context_lines)
        end = min(len(lines), index + context_lines + 1)
        ranges.append({
            "kind": "query",
            "selectors": matched,
            "start_line": start + 1,
            "end_line": end,
            "start_char": offsets[start],
            "end_char": offsets[end],
            "_score": len(matched) * 100 + (50 if normalized(query) in normalized(line) else 0),
        })
    # Do not let early incidental hits crowd out a later full phrase/term match.
    ranges.sort(key=lambda item: (-item["_score"], item["start_line"]))
    selected = ranges[:limit]
    for item in selected:
        item["_rank"] = item.pop("_score")
        if len(ranges) > len(selected):
            item["_query_truncated"] = True
    return selected


@index_snapshot
def fetch_document(
    root: Path,
    selector: str,
    clause_values: Iterable[str] | None,
    page_values: Iterable[str] | None,
    query: str | None,
    context_lines: int = 2,
    max_chars: int = 12000,
) -> dict[str, Any]:
    document, candidates = _resolve_document(root, selector)
    if not document:
        return {
            "document": selector,
            "error": "document_not_found_or_ambiguous",
            "candidates": candidates,
            "excerpts": [],
            "complete": False,
        }
    clauses = _parse_selectors(clause_values)
    pages = _parse_pages(page_values)
    result: dict[str, Any] = {
        "document": {
            "id": document["id"],
            "designation": document.get("short_title"),
            "title": document.get("title"),
            "status": document.get("status"),
            "lifecycle_stage": document.get("lifecycle_stage"),
            "path": document.get("path"),
            "normalized_path": document.get("normalized_path"),
            "source_pages": document.get("source_pages"),
            "extraction_confidence": document.get("extraction_confidence"),
            "verification": document.get("verification", {}),
        },
        "requested": {"clauses": clauses, "pages": pages, "query": query or None},
        "excerpts": [],
        "not_found": {"clauses": [], "pages": []},
        "ambiguous": {"clauses": []},
        "complete": True,
        "truncated": False,
    }
    if not clauses and not pages and not query:
        return result
    normalized_path = document.get("normalized_path")
    if not normalized_path:
        result.update({"error": "normalized_text_unavailable", "complete": False})
        return result
    source_path = (root / str(normalized_path)).resolve()
    if root.resolve() not in source_path.parents or not source_path.is_file():
        result.update({"error": "normalized_text_missing", "complete": False})
        return result
    text = source_path.read_text(encoding="utf-8", errors="replace")
    structural = current_structure(root, source_path)
    result["document"]["source_quality"] = structural.get("source_quality", "unknown")
    ranges: list[dict[str, Any]] = []
    for clause in clauses:
        parsed_clause = _parse_clause_selector(clause)
        if not parsed_clause:
            result["not_found"]["clauses"].append(clause)
            continue
        clause_number, anchor = parsed_clause
        matches = [item for item in structural.get("clauses", []) if item.get("clause") == clause_number]
        if anchor:
            matches = [item for item in matches if item.get("anchor") == anchor]
        if not matches:
            result["not_found"]["clauses"].append(clause)
        elif len(matches) > 1:
            # Annexes often restart numbering.  A bare selector cannot safely choose
            # between those distinct anchors.
            result["ambiguous"]["clauses"].append({
                "clause": clause_number,
                "anchors": [item.get("anchor") for item in matches],
            })
            continue
        for match in matches:
            ranges.append({
                "kind": "clause",
                "selectors": [clause],
                "start_line": match["start_line"],
                "end_line": match["end_line"],
                "start_char": match["start_char"],
                "end_char": match["end_char"],
                "page": match.get("page"),
                "end_page": match.get("end_page"),
                "title": match.get("title"),
            })
    for page in pages:
        matches = [item for item in structural.get("pages", []) if item.get("page") == page]
        if not matches:
            result["not_found"]["pages"].append(page)
        for match in matches:
            ranges.append({
                "kind": "page",
                "selectors": [str(page)],
                "start_line": match["start_line"],
                "end_line": match["end_line"],
                "start_char": match["start_char"],
                "end_char": match["end_char"],
                "page": page,
                "end_page": page,
            })
    if query:
        query_ranges = _query_ranges(text, query, max(0, min(int(context_lines), 20)))
        if not query_ranges:
            result["not_found"]["query"] = str(query)
        if any(item.get("_query_truncated") for item in query_ranges):
            result["truncated"] = True
        # When a hit belongs to one unambiguous clause, return the clause as the
        # useful normative context rather than an arbitrary few lines.
        for item in query_ranges:
            containing = [clause for clause in structural.get("clauses", [])
                          if clause["start_line"] <= item["start_line"] <= clause["end_line"]]
            if len(containing) == 1:
                clause = containing[0]
                item.update({
                    "start_line": clause["start_line"], "end_line": clause["end_line"],
                    "start_char": clause["start_char"], "end_char": clause["end_char"],
                    "title": clause.get("title"), "page": clause.get("page"),
                    "end_page": clause.get("end_page"),
                })
            item.pop("_query_truncated", None)
        ranges.extend(query_ranges)
    ranges = _merge_ranges(ranges)
    remaining = max(500, min(int(max_chars), 50000))
    for item in ranges:
        item.pop("_rank", None)
        excerpt_text = text[item["start_char"]:item["end_char"]].strip()
        if not excerpt_text:
            continue
        was_truncated = len(excerpt_text) > remaining
        if was_truncated:
            excerpt_text = excerpt_text[:remaining].rstrip()
        result["excerpts"].append({
            "kind": item.get("kind", "combined"),
            "selectors": item["selectors"],
            "title": item.get("title"),
            "page": item.get("page") or _page_for_line(structural.get("pages", []), item["start_line"]),
            "end_page": item.get("end_page") or _page_for_line(structural.get("pages", []), item["end_line"]),
            "start_line": item["start_line"],
            "end_line": item["end_line"],
            "source": normalized_path,
            "source_sha256": structural.get("sha256"),
            "text": excerpt_text,
            "truncated": was_truncated,
        })
        remaining -= len(excerpt_text)
        if remaining <= 0 or was_truncated:
            result["truncated"] = True
            break
    if len(result["excerpts"]) < len(ranges):
        result["truncated"] = True
    if query and not result["excerpts"]:
        # A successful lexical match is not useful if no bounded excerpt could be
        # returned (for example, an empty range after source damage).
        result["complete"] = False
    if (result["not_found"]["clauses"] or result["not_found"]["pages"]
            or result["not_found"].get("query") or result["ambiguous"]["clauses"] or result["truncated"]):
        result["complete"] = False
    return result


def _reference_resolution(root: Path, label: str) -> dict[str, Any]:
    exact, family = canonical_identifier(label)
    documents = _documents_by_id(root)
    exact_matches = [doc_id for doc_id, item in documents.items() if exact and item.get("canonical_exact") == exact]
    family_matches = [doc_id for doc_id, item in documents.items() if family and item.get("canonical_family") == family]
    explicit_edition_missing = bool(exact and family and exact != family and not exact_matches)
    matches = exact_matches if explicit_edition_missing or exact_matches else family_matches
    replacements = read_yaml(index_path(root, "meta/replacements.yaml")).get("replacements", [])
    replacement_matches = [
        {"old_document": item.get("old_document"), "replacement_document": item.get("replacement_document"), "status": item.get("status")}
        for item in replacements
        if normalized(label) in normalized(item.get("old_document")) or (family and family == item.get("canonical_key"))
    ][:5]
    return {
        "label": label,
        "canonical_exact": exact,
        "canonical_family": family,
        "status": (
            "missing_explicit_edition" if explicit_edition_missing else
            "resolved" if len(matches) == 1 else "ambiguous" if len(matches) > 1 else "missing"
        ),
        "target_document": matches[0] if len(matches) == 1 else None,
        "candidates": matches[:10],
        "available_editions": family_matches[:10] if explicit_edition_missing else [],
        "replacements": replacement_matches,
    }


@index_snapshot
def archive_context(
    root: Path,
    stage_id: str,
    query: str | None,
    references: Iterable[str] | None,
    max_chars: int = 16000,
) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", stage_id):
        raise ValueError("Некорректный stage_id")
    stage_dir = (root / "staging" / stage_id).resolve()
    if root.resolve() not in stage_dir.parents:
        raise ValueError("stage_id выходит за пределы базы")
    manifest = read_yaml(stage_dir / "manifest.yaml")
    if not manifest:
        raise FileNotFoundError(f"Не найден staging/{stage_id}/manifest.yaml")
    search_query = str(query or Path(str(manifest.get("source_name") or "")).stem).strip()
    categories = [
        {"id": item.get("id"), "title": item.get("title"), "description": item.get("description")}
        for item in read_yaml(root / "meta" / "categories.yaml").get("categories", [])
    ]
    plugin_reference = Path(__file__).resolve().parents[1] / "skills" / "archive-nuclear-documents" / "references" / "reference-contract.md"
    if not plugin_reference.is_file():
        plugin_reference = root / "plugin-src" / "skills" / "archive-nuclear-documents" / "references" / "reference-contract.md"
    result: dict[str, Any] = {
        "stage": {
            key: manifest.get(key)
            for key in (
                "stage_id", "state", "received_at", "source_name", "source_sha256", "source_extension",
                "staged_source", "extracted_text", "extraction_method", "characters_extracted",
                "security_status", "security_report", "security_report_sha256",
                "requires_visual_review", "warning",
            )
        },
        "resources": {
            "archivist_prompt": "prompts/archivist.md",
            "card_template": "docs/_templates/normative-document.md",
            "reference_contract": str(plugin_reference.resolve()),
        },
        "corpus": read_yaml(index_path(root, "meta/corpus-manifest.yaml")),
        "categories": categories,
        "duplicate_candidates": search_documents(root, search_query, limit=6, max_chars=8000)["results"] if search_query else [],
        "reference_resolutions": [_reference_resolution(root, label) for label in unique_strings(references or [])[:50]],
        "context_policy": "Компактный контекст без полных реестров cross-references и addition-queue.",
        "truncated": False,
    }
    budget = max(2000, min(int(max_chars), 50000))
    while result["duplicate_candidates"] and len(compact_json(result)) > budget:
        result["duplicate_candidates"].pop()
        result["truncated"] = True
    while result["categories"] and len(compact_json(result)) > budget:
        result["categories"].pop()
        result["truncated"] = True
    while result["reference_resolutions"] and len(compact_json(result)) > budget:
        result["reference_resolutions"].pop()
        result["truncated"] = True
    return result
