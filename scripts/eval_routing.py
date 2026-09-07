#!/usr/bin/env python3
"""Score routed answers against the same contracts used by a Sol/high baseline."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import Any

import yaml

from model_router import evaluation_identity


TOKEN_PATTERN = re.compile(r"\w+|[^\w\s]", re.UNICODE)


def read_data(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    loaded = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    return loaded if isinstance(loaded, dict) else {}


def ratio_found(answer: str, values: list[Any]) -> float:
    if not values:
        return 1.0
    lower = answer.lower()
    return sum(str(value).lower() in lower for value in values) / len(values)


def score_answer(answer: str, contract: dict[str, Any]) -> dict[str, Any]:
    min_chars = max(1, int(contract.get("min_chars", 1)))
    completeness = min(1.0, len(answer.strip()) / min_chars)
    requirements = ratio_found(answer, contract.get("required_strings", []))
    sections = ratio_found(answer, contract.get("required_sections", []))
    evidence = ratio_found(answer, contract.get("evidence_ids", []))
    forbidden = 1.0 - ratio_found(answer, contract.get("forbidden_strings", [])) if contract.get("forbidden_strings") else 1.0
    expected_format = contract.get("format", "text")
    format_score = 1.0
    if expected_format == "json":
        try:
            json.loads(answer)
        except Exception:
            format_score = 0.0
    dimensions = {
        "completeness": completeness,
        "requirements": requirements,
        "sections": sections,
        # An identifier string only proves that the answer contains that
        # string.  It is useful for a format/contract smoke test, never a
        # factual-grounding or expert-review score.
        "evidence_id_presence": evidence,
        "forbidden_claims": forbidden,
        "format": format_score,
    }
    return {"score": sum(dimensions.values()) / len(dimensions), "dimensions": dimensions}


def answer_sha256(answer: str) -> str:
    return hashlib.sha256(answer.encode("utf-8")).hexdigest()


def _reviewed_scores(
    dataset: dict[str, Any],
    cases: dict[str, dict[str, Any]],
    routed_cases: dict[str, dict[str, Any]],
    sol_cases: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate human review records bound to the exact two answer texts."""
    grades = dataset.get("reviewer_grades")
    if not isinstance(grades, list):
        raise ValueError("reviewed_answers требует reviewer_grades как массив")
    indexed: dict[str, dict[str, Any]] = {}
    for grade in grades:
        if not isinstance(grade, dict) or not isinstance(grade.get("id"), str):
            raise ValueError("reviewer_grades содержит запись без id")
        case_id = grade["id"]
        if case_id in indexed:
            raise ValueError(f"reviewer_grades содержит повтор id {case_id!r}")
        indexed[case_id] = grade
    if set(indexed) != set(cases):
        raise ValueError(
            "reviewer_grades должен содержать ровно по одной оценке на каждый сценарий"
        )

    comparisons: list[dict[str, Any]] = []
    for case_id in cases:
        grade = indexed[case_id]
        row: dict[str, Any] = {"id": case_id, "evaluation_source": "reviewer_grading"}
        for side, responses in (("routed", routed_cases), ("sol", sol_cases)):
            review = grade.get(side)
            if not isinstance(review, dict):
                raise ValueError(f"reviewer_grades[{case_id}].{side} отсутствует")
            reviewer = str(review.get("reviewer_id") or "").strip()
            reviewed_at = str(review.get("reviewed_at") or "").strip()
            rubric_version = str(review.get("rubric_version") or "").strip()
            answer_hash = str(review.get("answer_sha256") or "").strip().lower()
            if not reviewer or not reviewed_at or not rubric_version:
                raise ValueError(
                    f"reviewer_grades[{case_id}].{side} требует reviewer_id, reviewed_at и rubric_version"
                )
            if not re.fullmatch(r"[a-f0-9]{64}", answer_hash):
                raise ValueError(f"reviewer_grades[{case_id}].{side}: некорректный answer_sha256")
            actual_hash = answer_sha256(str(responses[case_id].get("answer", "")))
            if answer_hash != actual_hash:
                raise ValueError(
                    f"reviewer_grades[{case_id}].{side} устарела: answer_sha256 не совпадает"
                )
            try:
                score = float(review["score"])
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(f"reviewer_grades[{case_id}].{side} требует score 0..1") from error
            if not 0 <= score <= 1:
                raise ValueError(f"reviewer_grades[{case_id}].{side}: score вне 0..1")
            row[f"{side}_score"] = round(score, 6)
            # Retain enough provenance to audit a gate, not an answer or a
            # user/prompt identity.  Gate scores originate only here.
            row[f"{side}_review"] = {
                "reviewer_id": reviewer,
                "reviewed_at": reviewed_at,
                "rubric_version": rubric_version,
                "answer_sha256": answer_hash,
            }
        comparisons.append(row)
    return comparisons


def indexed_cases(payload: dict[str, Any], name: str) -> dict[str, dict[str, Any]]:
    cases = payload.get("cases", [])
    if not isinstance(cases, list):
        raise ValueError(f"{name}.cases должен быть массивом")
    result = {str(item.get("id")): item for item in cases if isinstance(item, dict) and item.get("id")}
    if len(result) != len(cases):
        raise ValueError(f"{name}.cases содержит дубли или записи без id")
    return result


def _reported_tokens(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} должен быть неотрицательным целым числом")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} должен быть неотрицательным целым числом") from error
    if not math.isfinite(numeric) or numeric < 0 or not numeric.is_integer():
        raise ValueError(f"{field} должен быть неотрицательным целым числом")
    return int(numeric)


def response_tokens(item: dict[str, Any]) -> tuple[int, str]:
    reported = _reported_tokens(item.get("output_tokens"), "output_tokens")
    if reported is not None:
        return reported, "reported"
    answer = str(item.get("answer", ""))
    estimate = max(1, round(len(TOKEN_PATTERN.findall(answer)) * 1.35)) if answer else 0
    return estimate, "estimated"


def evaluate(
    dataset: dict[str, Any],
    routed: dict[str, Any],
    sol: dict[str, Any],
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    cases = indexed_cases(dataset, "dataset")
    routed_cases = indexed_cases(routed, "routed")
    sol_cases = indexed_cases(sol, "sol")
    missing_routed = sorted(set(cases) - set(routed_cases))
    missing_sol = sorted(set(cases) - set(sol_cases))
    if missing_routed or missing_sol:
        raise ValueError(f"Нет ответов: routed={missing_routed}, sol={missing_sol}")
    evaluation_kind = str(dataset.get("evaluation_kind") or "format_only")
    if evaluation_kind not in {"format_only", "reviewed_answers"}:
        raise ValueError(
            f"Набор {evaluation_kind!r} не является завершённой оценкой; ожидается format_only или reviewed_answers"
        )
    if evaluation_kind == "reviewed_answers":
        if root is None:
            raise ValueError("reviewed_answers требует root для проверки identity корпуса и плагина")
        identity = evaluation_identity(root)
        if dataset.get("identity") != identity:
            raise ValueError(
                "reviewed_answers требует dataset.identity, совпадающий с текущей identity корпуса и плагина"
            )
        comparisons = _reviewed_scores(dataset, cases, routed_cases, sol_cases)
    else:
        comparisons = []
        for case_id, case in cases.items():
            contract = case.get("contract", {})
            routed_score = score_answer(str(routed_cases[case_id].get("answer", "")), contract)
            sol_score = score_answer(str(sol_cases[case_id].get("answer", "")), contract)
            comparisons.append({
                "id": case_id,
                "evaluation_source": "format_contract",
                "routed_model": routed_cases[case_id].get("model"),
                "routed_score": round(routed_score["score"], 6),
                "sol_score": round(sol_score["score"], 6),
                "routed_dimensions": routed_score["dimensions"],
                "sol_dimensions": sol_score["dimensions"],
            })
        identity = evaluation_identity(root) if root is not None else None
    routed_output = [response_tokens(item) for item in routed_cases.values()]
    sol_output = [response_tokens(item) for item in sol_cases.values()]
    routed_inputs = [_reported_tokens(item.get("input_tokens"), "input_tokens") for item in routed_cases.values()]
    sol_inputs = [_reported_tokens(item.get("input_tokens"), "input_tokens") for item in sol_cases.values()]
    routed_input = sum(routed_inputs) if all(value is not None for value in routed_inputs) else None
    sol_input = sum(sol_inputs) if all(value is not None for value in sol_inputs) else None
    outputs_reported = all(source == "reported" for _, source in routed_output + sol_output)
    inputs_reported = routed_input is not None and sol_input is not None
    fully_reported = inputs_reported and outputs_reported
    token_source = "reported" if fully_reported else "partial"
    routed_output_total = sum(value for value, _ in routed_output)
    sol_output_total = sum(value for value, _ in sol_output)
    combined_input = routed_input + sol_input if inputs_reported else None
    combined_output = routed_output_total + sol_output_total
    routed_total = routed_input + routed_output_total if routed_input is not None and all(source == "reported" for _, source in routed_output) else None
    sol_total = sol_input + sol_output_total if sol_input is not None and all(source == "reported" for _, source in sol_output) else None
    combined_total = combined_input + combined_output if fully_reported and combined_input is not None else None
    latency_values = [_reported_tokens(item.get("latency_ms"), "latency_ms")
                      for item in [*routed_cases.values(), *sol_cases.values()]]
    result = {
        "schema_version": 2,
        "dataset": dataset.get("name"),
        "evaluation_kind": evaluation_kind,
        "comparisons": comparisons,
        "tokens": {
            "input": combined_input,
            "output": combined_output,
            "total": combined_total,
            "source": token_source,
            "routed": {
                "input": routed_input,
                "output": routed_output_total,
                "total": routed_total,
            },
            "sol_baseline": {
                "input": sol_input,
                "output": sol_output_total,
                "total": sol_total,
            },
            "output_delta_routed_minus_sol": routed_output_total - sol_output_total if outputs_reported else None,
            "total_delta_routed_minus_sol": routed_total - sol_total if fully_reported and routed_total is not None and sol_total is not None else None,
        },
        "latency_ms": sum(latency_values) if all(value is not None for value in latency_values) else None,
    }
    if identity is not None:
        result["identity"] = identity
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Сравнить routed-ответы с Sol/high baseline")
    parser.add_argument("dataset")
    parser.add_argument("routed")
    parser.add_argument("sol")
    parser.add_argument("output")
    parser.add_argument(
        "--kb-root",
        type=Path,
        default=Path(os.environ["NV2_NUCLEAR_KB_ROOT"]) if os.environ.get("NV2_NUCLEAR_KB_ROOT") else None,
        help="Корень базы; обязателен для reviewed_answers.",
    )
    args = parser.parse_args()
    result = evaluate(
        read_data(Path(args.dataset)),
        read_data(Path(args.routed)),
        read_data(Path(args.sol)),
        root=args.kb_root,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml.safe_dump(result, allow_unicode=True, sort_keys=False, width=100), encoding="utf-8")
    print(json.dumps({
        "dataset": result["dataset"],
        "evaluation_kind": result["evaluation_kind"],
        "cases": len(result["comparisons"]),
        "output": str(output.resolve()),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
