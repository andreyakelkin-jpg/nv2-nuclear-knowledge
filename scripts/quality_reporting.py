"""Read-only quality/workload summaries, without changing expert approval."""
from __future__ import annotations

import json
from pathlib import Path

from document_usage import document_usage_statistics
from index_store import index_path, index_snapshot
from kb_root import resolve_state_root
from model_router import routing_status
from retrieval import read_json, read_yaml


@index_snapshot
def review_priorities(root: Path, limit: int = 20) -> dict:
    usage = document_usage_statistics(resolve_state_root(root), root)
    counts = {doc_id: item for item in usage["documents"] for doc_id in item.get("document_ids", [])}
    documents = read_yaml(index_path(root)).get("documents", [])
    pending = []
    for doc in documents:
        if doc.get("lifecycle_stage") == "approved_for_operational_use":
            continue
        count = counts.get(doc["id"], {})
        pending.append({"document_id": doc["id"], "title": doc.get("short_title"),
                        "used_count": count.get("used_count", 0), "potential_count": count.get("potential_count", 0),
                        "lifecycle_stage": doc.get("lifecycle_stage"), "verification": doc.get("verification", {}),
                        "extraction_confidence": doc.get("extraction_confidence")})
    pending.sort(key=lambda item: (-item["used_count"], -item["potential_count"], item["document_id"]))
    return {"pending_count": len(pending), "results": pending[:max(1, min(100, limit))],
            "review_checklist": ["Проверить актуальность редакции по первоисточнику", "Сверить пункты, таблицы и страницы с оригиналом",
                                 "Зафиксировать эксперта, дату и источник проверки; автоматического допуска нет"]}


@index_snapshot
def quality_status(root: Path) -> dict:
    path = resolve_state_root(root) / "reports/model-routing/events.jsonl"
    events, malformed = [], 0
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
                if event.get("event") == "route_checked":
                    events.append(event)
            except (ValueError, AttributeError):
                malformed += 1
    reported = [event for event in events if event.get("tokens", {}).get("source") == "reported"]
    timings = {}
    for phase in ("total", "retrieval", "generation", "validation"):
        values = sorted(float(event.get("timings_ms", {}).get(phase)) for event in events
                        if isinstance(event.get("timings_ms", {}).get(phase), (int, float)))
        timings[phase] = {"samples": len(values), "median_ms": values[len(values)//2] if values else None,
                          "p95_ms": values[min(len(values)-1, int(len(values)*.95))] if values else None}
    structural = read_json(index_path(root, "meta/clause-index.json")).get("documents", {})
    return {"routing": routing_status(root), "review": review_priorities(root, 10),
            "retrieval": {"indexed_documents": len(structural),
                          "unknown_page_mapping": sum(not item.get("pages") for item in structural.values())},
            "telemetry": {"checked_answers": len(events), "reported_token_samples": len(reported),
                          "unreported_or_estimated_samples": len(events)-len(reported),
                          "malformed_log_lines": malformed, "timings_ms": timings,
                          "note": "Unknown tokens are not zero; estimates do not establish real savings."}}
