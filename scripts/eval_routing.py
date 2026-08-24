#!/usr/bin/env python3
"""Score routed answers against the same contracts used by a Sol/high baseline."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import yaml


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
        "factual_grounding": evidence,
        "forbidden_claims": forbidden,
        "format": format_score,
    }
    return {"score": sum(dimensions.values()) / len(dimensions), "dimensions": dimensions}


def indexed_cases(payload: dict[str, Any], name: str) -> dict[str, dict[str, Any]]:
    cases = payload.get("cases", [])
    if not isinstance(cases, list):
        raise ValueError(f"{name}.cases должен быть массивом")
    result = {str(item.get("id")): item for item in cases if isinstance(item, dict) and item.get("id")}
    if len(result) != len(cases):
        raise ValueError(f"{name}.cases содержит дубли или записи без id")
    return result


def response_tokens(item: dict[str, Any]) -> tuple[int, str]:
    reported = item.get("output_tokens")
    if reported is not None:
        return max(0, int(reported)), "reported"
    answer = str(item.get("answer", ""))
    estimate = max(1, round(len(TOKEN_PATTERN.findall(answer)) * 1.35)) if answer else 0
    return estimate, "estimated"


def evaluate(dataset: dict[str, Any], routed: dict[str, Any], sol: dict[str, Any]) -> dict[str, Any]:
    cases = indexed_cases(dataset, "dataset")
    routed_cases = indexed_cases(routed, "routed")
    sol_cases = indexed_cases(sol, "sol")
    missing_routed = sorted(set(cases) - set(routed_cases))
    missing_sol = sorted(set(cases) - set(sol_cases))
    if missing_routed or missing_sol:
        raise ValueError(f"Нет ответов: routed={missing_routed}, sol={missing_sol}")
    comparisons = []
    for case_id, case in cases.items():
        contract = case.get("contract", {})
        routed_score = score_answer(str(routed_cases[case_id].get("answer", "")), contract)
        sol_score = score_answer(str(sol_cases[case_id].get("answer", "")), contract)
        comparisons.append({
            "id": case_id,
            "routed_model": routed_cases[case_id].get("model"),
            "routed_score": round(routed_score["score"], 6),
            "sol_score": round(sol_score["score"], 6),
            "routed_dimensions": routed_score["dimensions"],
            "sol_dimensions": sol_score["dimensions"],
        })
    routed_output = [response_tokens(item) for item in routed_cases.values()]
    sol_output = [response_tokens(item) for item in sol_cases.values()]
    routed_input = sum(int(item.get("input_tokens", 0) or 0) for item in routed_cases.values())
    sol_input = sum(int(item.get("input_tokens", 0) or 0) for item in sol_cases.values())
    token_source = "reported" if all(source == "reported" for _, source in routed_output + sol_output) else "reported_and_estimated"
    routed_output_total = sum(value for value, _ in routed_output)
    sol_output_total = sum(value for value, _ in sol_output)
    combined_input = routed_input + sol_input
    combined_output = routed_output_total + sol_output_total
    return {
        "schema_version": 1,
        "dataset": dataset.get("name"),
        "comparisons": comparisons,
        "tokens": {
            "input": combined_input,
            "output": combined_output,
            "total": combined_input + combined_output,
            "source": token_source,
            "routed": {
                "input": routed_input,
                "output": routed_output_total,
                "total": routed_input + routed_output_total,
            },
            "sol_baseline": {
                "input": sol_input,
                "output": sol_output_total,
                "total": sol_input + sol_output_total,
            },
            "output_delta_routed_minus_sol": routed_output_total - sol_output_total,
        },
        "latency_ms": sum(int(item.get("latency_ms", 0) or 0) for item in routed_cases.values())
        + sum(int(item.get("latency_ms", 0) or 0) for item in sol_cases.values()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Сравнить routed-ответы с Sol/high baseline")
    parser.add_argument("dataset")
    parser.add_argument("routed")
    parser.add_argument("sol")
    parser.add_argument("output")
    args = parser.parse_args()
    result = evaluate(
        read_data(Path(args.dataset)),
        read_data(Path(args.routed)),
        read_data(Path(args.sol)),
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml.safe_dump(result, allow_unicode=True, sort_keys=False, width=100), encoding="utf-8")
    print(json.dumps({
        "dataset": result["dataset"],
        "cases": len(result["comparisons"]),
        "output": str(output.resolve()),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
