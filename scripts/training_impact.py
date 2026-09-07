#!/usr/bin/env python3
"""Read-only training impact index for changed normative sources."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from kb_root import resolve_kb_root


def _load(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError(f"YAML должен содержать объект: {path}")
    return value


def _references(definition: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    source = definition.get("source")
    values = source if isinstance(source, list) else [source]
    return [value for value in values if isinstance(value, Mapping) and isinstance(value.get("document_id"), str)]


def training_impact(root: Path, documents: Iterable[str | Mapping[str, Any]]) -> dict[str, Any]:
    """Return question/case IDs directly dependent on changed sources.

    The dependency record retains declared document ID and hashes, rather than
    guessing equivalence between source editions.
    """
    changed: dict[str, dict[str, Any]] = {}
    for document in documents:
        if isinstance(document, str):
            changed[document] = {"document_id": document}
        elif isinstance(document, Mapping) and isinstance(document.get("id"), str):
            changed[str(document["id"])] = {
                "document_id": str(document["id"]),
                "original_sha256": document.get("original_sha256") or document.get("source_sha256"),
                "normalized_sha256": document.get("normalized_sha256"),
            }
    affected: dict[str, list[dict[str, Any]]] = {document_id: [] for document_id in changed}
    for kind, directory in (("question", "question-bank"), ("case", "cases")):
        for path in sorted((root / "training" / directory).glob("*.yaml")):
            if path.name.startswith("_"):
                continue
            definition = _load(path)
            item_id = definition.get("id")
            if not isinstance(item_id, str) or not item_id:
                continue
            for source in _references(definition):
                document_id = str(source["document_id"])
                if document_id in affected:
                    affected[document_id].append({
                        "kind": kind, "id": item_id,
                        "source": {"document_id": document_id, "sha256": source.get("sha256"),
                                   "original_sha256": source.get("original_sha256"),
                                   "normalized_sha256": source.get("normalized_sha256")},
                    })
    impacts = [{**changed[document_id], "affected": sorted(affected[document_id], key=lambda item: (item["kind"], item["id"]))}
               for document_id in sorted(changed)]
    return {
        "impacts": impacts,
        "affected_question_ids": sorted(item["id"] for entries in affected.values() for item in entries if item["kind"] == "question"),
        "affected_case_ids": sorted(item["id"] for entries in affected.values() for item in entries if item["kind"] == "case"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document_id", nargs="+", help="Changed document id(s)")
    args = parser.parse_args(argv)
    print(json.dumps(training_impact(resolve_kb_root(), args.document_id), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
