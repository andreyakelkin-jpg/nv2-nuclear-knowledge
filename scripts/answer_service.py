"""One recoverable final-answer operation: validate, then count usage exactly once."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from document_usage import record_document_usage
from kb_root import resolve_state_root
from model_router import _run_path, check_route, read_yaml, write_yaml
from write_lock import exclusive_file_lock


def finish_answer(root: Path, run_id: str, *, answer_path: Path, contract_path: Path,
                  used: list[str], missing: list[str], **measurement) -> dict:
    state_root = resolve_state_root(root)
    payload = {"answer": answer_path.read_text(encoding="utf-8"),
               "contract": contract_path.read_text(encoding="utf-8"),
               "used": sorted(set(used)), "missing": sorted(set(missing))}
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    path = _run_path(root, run_id)
    with exclusive_file_lock(state_root / ".locks" / f"finish-{run_id}.lock", "finish answer"):
        state = read_yaml(path)
        if not state:
            raise ValueError("Routing run not found")
        previous = state.get("finish_request_sha256")
        if previous and previous != fingerprint:
            raise ValueError("A finished answer cannot be changed; use a new routing run")
        if state.get("completed_at") and not previous:
            raise ValueError("Run was already checked; use usage-record for that validated answer")
        if not state.get("completed_at"):
            state["finish_request_sha256"] = fingerprint
            write_yaml(path, state)
            result = check_route(root, run_id, answer_path=answer_path, contract_path=contract_path, **measurement)
        else:
            result = {"run_id": run_id, "accepted": state.get("validation", {}).get("passed", False),
                      "validation": state.get("validation"), "tokens": state.get("tokens"),
                      "escalate_once": state.get("escalation_recommended"), "replayed": True}
        if result["accepted"] and (used or missing):
            with exclusive_file_lock(state_root / ".locks" / "document-usage.lock", "record answer usage"):
                result["usage"] = record_document_usage(state_root, root, {
                    "schema_version": 1, "answer_id": run_id,
                    "used_documents": used, "missing_documents": missing,
                })
        return result
