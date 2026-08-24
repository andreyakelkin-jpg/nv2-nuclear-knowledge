#!/usr/bin/env python3
"""Deterministic, fail-closed model routing and quality gates for Codex workers."""
from __future__ import annotations

import hashlib
import json
import random
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from kb_root import read_config, update_config


LEVELS = {"low": 0, "medium": 1, "high": 2}
TIERS = (
    {"tier": "luna", "model": "gpt-5.6-luna", "effort": "low"},
    {"tier": "terra", "model": "gpt-5.6-terra", "effort": "medium"},
    {"tier": "sol", "model": "gpt-5.6-sol", "effort": "high"},
)
DEFAULT_ROUTING = {
    "enabled": False,
    "require_quality_gate": True,
    "non_inferiority_margin": 0.05,
    "minimum_eval_cases": 12,
}
TOKEN_PATTERN = re.compile(r"\w+|[^\w\s]", re.UNICODE)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100), encoding="utf-8")


def _routing_config() -> dict[str, Any]:
    configured = read_config().get("routing", {})
    if not isinstance(configured, dict):
        configured = {}
    return {**DEFAULT_ROUTING, **configured}


def _gate_path(root: Path) -> Path:
    return root / "reports" / "model-routing" / "gate-latest.yaml"


def _log_path(root: Path) -> Path:
    return root / "reports" / "model-routing" / "events.jsonl"


def _run_path(root: Path, run_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9-]{36}", run_id):
        raise ValueError("Некорректный run_id")
    return root / "reports" / "model-routing" / "runs" / f"{run_id}.yaml"


def _append_event(root: Path, event: dict[str, Any]) -> None:
    path = _log_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")


def routing_status(root: Path) -> dict[str, Any]:
    config = _routing_config()
    gate = read_yaml(_gate_path(root))
    gate_passed = gate.get("status") == "passed"
    effective = bool(config["enabled"]) and (gate_passed or not config["require_quality_gate"])
    reason = "enabled"
    if not config["enabled"]:
        reason = "feature_flag_disabled"
    elif config["require_quality_gate"] and not gate_passed:
        reason = "quality_gate_not_passed"
    return {
        "configured_enabled": bool(config["enabled"]),
        "effective_enabled": effective,
        "reason": reason,
        "require_quality_gate": bool(config["require_quality_gate"]),
        "gate": gate or None,
        "fallback": TIERS[2],
    }


def set_routing_enabled(root: Path, enabled: bool) -> dict[str, Any]:
    config = _routing_config()
    config["enabled"] = bool(enabled)
    update_config({"routing": config})
    return routing_status(root)


def _context_level(context_chars: int) -> str:
    if context_chars <= 12000:
        return "low"
    if context_chars <= 80000:
        return "medium"
    return "high"


def _tools_level(tool_count: int) -> str:
    if tool_count <= 2:
        return "low"
    if tool_count <= 8:
        return "medium"
    return "high"


def assess_route(
    *,
    complexity: str,
    ambiguity: str,
    criticality: str,
    context_chars: int,
    tool_count: int,
    side_effects: str,
    confidence: float,
) -> dict[str, Any]:
    for name, value in (("complexity", complexity), ("ambiguity", ambiguity), ("criticality", criticality)):
        if value not in LEVELS:
            raise ValueError(f"{name}: допустимы low, medium, high")
    if side_effects not in {"none", "local", "external"}:
        raise ValueError("side_effects: допустимы none, local, external")
    if not 0 <= confidence <= 1:
        raise ValueError("confidence должен быть от 0 до 1")
    if context_chars < 0 or tool_count < 0:
        raise ValueError("context_chars и tool_count не могут быть отрицательными")

    context = _context_level(context_chars)
    tools = _tools_level(tool_count)
    assessment = {
        "complexity": complexity,
        "ambiguity": ambiguity,
        "criticality": criticality,
        "context_volume": context,
        "context_chars": context_chars,
        "tools": tools,
        "tool_count": tool_count,
        "side_effects": side_effects,
        "confidence": round(confidence, 4),
    }
    reasons: list[str] = []
    hard_sol = []
    if complexity == "high": hard_sol.append("high_complexity")
    if ambiguity == "high": hard_sol.append("high_ambiguity")
    if criticality == "high": hard_sol.append("high_error_criticality")
    if context == "high": hard_sol.append("large_context")
    if side_effects == "external": hard_sol.append("external_side_effects")
    if confidence < 0.75: hard_sol.append("low_confidence")

    strictly_luna = (
        complexity == ambiguity == criticality == "low"
        and context == tools == "low"
        and side_effects == "none"
        and confidence >= 0.90
    )
    medium_count = sum(value == "medium" for value in (complexity, ambiguity, criticality, context, tools))
    borderline_sol = (
        not hard_sol
        and (
            (medium_count >= 4 and confidence < 0.85)
            or 60000 <= context_chars <= 80000
            or 0.75 <= confidence < 0.80
            or (side_effects == "local" and medium_count >= 3)
            or (tools == "high" and ambiguity == "medium" and confidence < 0.85)
            or (tools == "high" and side_effects == "local")
        )
    )

    if hard_sol:
        selected_index = 2
        reasons.extend(hard_sol)
    elif borderline_sol:
        selected_index = 2
        reasons.append("borderline_bumped_to_stronger_model")
    elif strictly_luna:
        selected_index = 0
        reasons.append("bounded_unambiguous_low_risk_verifiable")
    else:
        selected_index = 1
        reasons.append("balanced_default")
        if tools == "high":
            reasons.append("bounded_read_only_tool_volume")

    route_confidence = 0.98 if hard_sol or strictly_luna else 0.86
    if borderline_sol:
        route_confidence = 0.78
    return {
        **TIERS[selected_index],
        "tier_index": selected_index,
        "reason": reasons,
        "confidence": route_confidence,
        "assessment": assessment,
    }


def start_route(
    root: Path,
    *,
    task_fingerprint: str,
    complexity: str,
    ambiguity: str,
    criticality: str,
    context_chars: int,
    tool_count: int,
    side_effects: str,
    confidence: float,
    parent_run_id: str | None = None,
    force_tier_index: int | None = None,
) -> dict[str, Any]:
    status = routing_status(root)
    route = assess_route(
        complexity=complexity,
        ambiguity=ambiguity,
        criticality=criticality,
        context_chars=context_chars,
        tool_count=tool_count,
        side_effects=side_effects,
        confidence=confidence,
    )
    if force_tier_index is not None:
        if force_tier_index not in range(len(TIERS)):
            raise ValueError("Некорректный уровень принудительной эскалации")
        route.update(TIERS[force_tier_index])
        route["tier_index"] = force_tier_index
        route["reason"] = ["single_validation_escalation"]
    elif not status["effective_enabled"]:
        route.update(TIERS[2])
        route["tier_index"] = 2
        route["reason"] = [status["reason"], "fail_closed_to_sol"]
        route["confidence"] = 1.0

    run_id = str(uuid.uuid4())
    task_hash = hashlib.sha256(task_fingerprint.encode("utf-8")).hexdigest()
    started_at = utc_now()
    state = {
        "schema_version": 1,
        "run_id": run_id,
        "parent_run_id": parent_run_id,
        "started_at": started_at,
        "task_hash": task_hash,
        "route": route,
        "routing_status": status,
        "escalation": parent_run_id is not None,
        "escalation_count": 1 if parent_run_id else 0,
        "side_effects_committed": False,
    }
    write_yaml(_run_path(root, run_id), state)
    _append_event(root, {
        "timestamp": started_at,
        "event": "route_started",
        "run_id": run_id,
        "parent_run_id": parent_run_id,
        "task_hash": task_hash,
        "model": route["model"],
        "effort": route["effort"],
        "reason": route["reason"],
        "confidence": route["confidence"],
        "escalation": state["escalation"],
        "tokens": {"input": None, "output": None, "total": None, "source": "pending"},
        "latency_ms": 0,
    })
    return {
        "run_id": run_id,
        "model": route["model"],
        "effort": route["effort"],
        "reason": route["reason"],
        "confidence": route["confidence"],
        "assessment": route["assessment"],
        "escalation": state["escalation"],
        "routing_enabled": status["effective_enabled"],
        "side_effect_policy": "Draft and validate first; commit side effects once after acceptance.",
    }


def _estimate_tokens(text: str) -> int:
    return max(1, round(len(TOKEN_PATTERN.findall(text)) * 1.35)) if text else 0


def validate_answer_text(answer: str, contract: dict[str, Any]) -> dict[str, Any]:
    failures: dict[str, list[str]] = {"completeness": [], "factual_grounding": [], "format": [], "requirements": []}
    if len(answer.strip()) < int(contract.get("min_chars", 1)):
        failures["completeness"].append("answer_too_short")
    lower = answer.lower()
    for value in contract.get("required_strings", []):
        if str(value).lower() not in lower:
            failures["requirements"].append(f"missing:{value}")
    for value in contract.get("required_sections", []):
        if str(value).lower() not in lower:
            failures["format"].append(f"missing_section:{value}")
    for value in contract.get("evidence_ids", []):
        if str(value).lower() not in lower:
            failures["factual_grounding"].append(f"missing_evidence:{value}")
    for value in contract.get("forbidden_strings", []):
        if str(value).lower() in lower:
            failures["requirements"].append(f"forbidden:{value}")
    expected_format = contract.get("format", "text")
    if expected_format == "json":
        try:
            json.loads(answer)
        except Exception:
            failures["format"].append("invalid_json")
    elif expected_format not in {"text", "markdown"}:
        failures["format"].append(f"unsupported_contract_format:{expected_format}")
    checks = {name: not items for name, items in failures.items()}
    return {"passed": all(checks.values()), "checks": checks, "failures": failures}


def check_route(
    root: Path,
    run_id: str,
    *,
    answer_path: Path,
    contract_path: Path,
    input_path: Path | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    latency_ms: int | None = None,
) -> dict[str, Any]:
    state_path = _run_path(root, run_id)
    state = read_yaml(state_path)
    if not state:
        raise FileNotFoundError(f"Не найден routing run {run_id}")
    if state.get("completed_at"):
        raise ValueError("Routing run уже завершён; повторная проверка запрещена")
    answer = answer_path.read_text(encoding="utf-8")
    contract = read_yaml(contract_path)
    validation = validate_answer_text(answer, contract)
    input_text = input_path.read_text(encoding="utf-8") if input_path else ""
    resolved_input_tokens = input_tokens if input_tokens is not None else _estimate_tokens(input_text)
    resolved_output_tokens = output_tokens if output_tokens is not None else _estimate_tokens(answer)
    token_source = "reported" if input_tokens is not None and output_tokens is not None else "estimated"
    if latency_ms is None:
        started = datetime.fromisoformat(str(state["started_at"]))
        measured_latency = max(0, round((datetime.now(timezone.utc) - started).total_seconds() * 1000))
    else:
        measured_latency = max(0, latency_ms)
    tier_index = int(state["route"]["tier_index"])
    can_escalate = not validation["passed"] and not state.get("escalation") and tier_index < 2
    escalation = TIERS[tier_index + 1] if can_escalate else None
    completed_at = utc_now()
    state.update({
        "completed_at": completed_at,
        "validation": validation,
        "tokens": {
            "input": resolved_input_tokens,
            "output": resolved_output_tokens,
            "total": resolved_input_tokens + resolved_output_tokens,
            "source": token_source,
        },
        "latency_ms": measured_latency,
        "escalation_recommended": escalation,
    })
    write_yaml(state_path, state)
    _append_event(root, {
        "timestamp": completed_at,
        "event": "route_checked",
        "run_id": run_id,
        "parent_run_id": state.get("parent_run_id"),
        "task_hash": state.get("task_hash"),
        "model": state["route"]["model"],
        "effort": state["route"]["effort"],
        "reason": state["route"]["reason"],
        "confidence": state["route"]["confidence"],
        "escalation": bool(state.get("escalation")),
        "validation": validation,
        "tokens": state["tokens"],
        "latency_ms": measured_latency,
    })
    return {
        "run_id": run_id,
        "accepted": validation["passed"],
        "validation": validation,
        "escalate_once": escalation,
        "tokens": state["tokens"],
        "latency_ms": measured_latency,
        "side_effects_allowed": validation["passed"],
    }


def escalate_route(root: Path, run_id: str) -> dict[str, Any]:
    state_path = _run_path(root, run_id)
    state = read_yaml(state_path)
    if not state or not state.get("completed_at"):
        raise ValueError("Сначала завершите проверку исходного routing run")
    escalation = state.get("escalation_recommended")
    if not escalation:
        raise ValueError("Эскалация не разрешена или уже исчерпана")
    if state.get("escalation_claimed_at"):
        raise ValueError("Однократная эскалация уже использована")
    assessment = state["route"]["assessment"]
    child = start_route(
        root,
        task_fingerprint=str(state["task_hash"]),
        complexity=assessment["complexity"],
        ambiguity=assessment["ambiguity"],
        criticality=assessment["criticality"],
        context_chars=int(assessment["context_chars"]),
        tool_count=int(assessment["tool_count"]),
        side_effects=assessment["side_effects"],
        confidence=float(assessment["confidence"]),
        parent_run_id=run_id,
        force_tier_index=int(state["route"]["tier_index"]) + 1,
    )
    state["escalation_claimed_at"] = utc_now()
    state["escalation_run_id"] = child["run_id"]
    write_yaml(state_path, state)
    return child


def claim_side_effects(root: Path, run_id: str, operation_id: str) -> dict[str, Any]:
    state_path = _run_path(root, run_id)
    state = read_yaml(state_path)
    if not state or not state.get("completed_at"):
        raise ValueError("Сначала завершите проверку routing run")
    if not state.get("validation", {}).get("passed"):
        raise ValueError("Побочные действия запрещены: ответ не прошёл проверку")
    if state.get("side_effects_committed"):
        raise ValueError("Побочные действия для этого run уже были разрешены; повтор запрещён")
    operation_hash = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()
    state["side_effects_committed"] = True
    state["side_effects_operation_hash"] = operation_hash
    state["side_effects_claimed_at"] = utc_now()
    write_yaml(state_path, state)
    _append_event(root, {
        "timestamp": state["side_effects_claimed_at"],
        "event": "side_effects_claimed",
        "run_id": run_id,
        "task_hash": state.get("task_hash"),
        "model": state["route"]["model"],
        "effort": state["route"]["effort"],
        "reason": ["validated_single_execution_claim"],
        "confidence": state["route"]["confidence"],
        "escalation": bool(state.get("escalation")),
        "tokens": state.get("tokens"),
        "latency_ms": state.get("latency_ms"),
        "operation_hash": operation_hash,
    })
    return {
        "run_id": run_id,
        "side_effects_allowed": True,
        "operation_hash": operation_hash,
        "execute_once": True,
    }


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(quantile * (len(ordered) - 1))))
    return ordered[index]


def non_inferiority_gate(
    comparisons: list[dict[str, Any]],
    *,
    margin: float,
    minimum_cases: int,
    bootstrap_samples: int = 20000,
) -> dict[str, Any]:
    if len(comparisons) < minimum_cases:
        return {
            "status": "failed",
            "reason": "insufficient_sample",
            "case_count": len(comparisons),
            "minimum_cases": minimum_cases,
            "margin": margin,
        }
    score_pairs = [(float(item["routed_score"]), float(item["sol_score"])) for item in comparisons]
    if any(not 0 <= score <= 1 for pair in score_pairs for score in pair):
        raise ValueError("Оценки качества должны находиться в диапазоне 0..1")
    differences = [routed - sol for routed, sol in score_pairs]
    rng = random.Random(5601)
    means = []
    for _ in range(max(1000, bootstrap_samples)):
        sample = [differences[rng.randrange(len(differences))] for _ in differences]
        means.append(sum(sample) / len(sample))
    mean_difference = sum(differences) / len(differences)
    lower_bound = _percentile(means, 0.05)
    passed = lower_bound >= -abs(margin)
    return {
        "status": "passed" if passed else "failed",
        "reason": "non_inferior" if passed else "quality_degradation_detected",
        "case_count": len(comparisons),
        "margin": abs(margin),
        "mean_routed_score": sum(float(item["routed_score"]) for item in comparisons) / len(comparisons),
        "mean_sol_score": sum(float(item["sol_score"]) for item in comparisons) / len(comparisons),
        "mean_difference": mean_difference,
        "one_sided_95pct_lower_bound": lower_bound,
        "bootstrap_samples": max(1000, bootstrap_samples),
    }


def apply_quality_gate(root: Path, comparisons_path: Path) -> dict[str, Any]:
    payload = read_yaml(comparisons_path)
    comparisons = payload.get("comparisons", [])
    if not isinstance(comparisons, list):
        raise ValueError("comparisons должен быть массивом")
    config = _routing_config()
    result = non_inferiority_gate(
        comparisons,
        margin=float(config["non_inferiority_margin"]),
        minimum_cases=int(config["minimum_eval_cases"]),
    )
    result.update({
        "evaluated_at": utc_now(),
        "baseline": "gpt-5.6-sol/high",
        "dataset": payload.get("dataset"),
    })
    write_yaml(_gate_path(root), result)
    config["enabled"] = result["status"] == "passed"
    update_config({"routing": config})
    _append_event(root, {
        "timestamp": result["evaluated_at"],
        "event": "quality_gate_applied",
        "model": "mixed" if result["status"] == "passed" else "gpt-5.6-sol",
        "effort": "mixed" if result["status"] == "passed" else "high",
        "reason": result["reason"],
        "confidence": result.get("one_sided_95pct_lower_bound"),
        "escalation": False,
        "tokens": payload.get("tokens", {"input": None, "output": None, "total": None, "source": "eval"}),
        "latency_ms": payload.get("latency_ms"),
    })
    return {"gate": result, "routing": routing_status(root)}
