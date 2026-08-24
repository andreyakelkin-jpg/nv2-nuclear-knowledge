#!/usr/bin/env python3
"""Рассчитывает итог аттестации по объективным вопросам и экспертным кейсам."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from kb_root import resolve_kb_root


ROOT = resolve_kb_root()


def load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def item_path(kind: str, item_id: str) -> Path:
    folder = "question-bank" if kind == "question" else "cases"
    return ROOT / "training" / folder / f"{item_id}.yaml"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("assignment")
    parser.add_argument("answers")
    args = parser.parse_args()
    assignment = load(Path(args.assignment))
    answers = load(Path(args.answers)).get("answers", {})
    weighted_score = 0.0
    total_weight = 0.0
    critical_errors = 0
    details = []
    for item in assignment.get("items", []):
        kind, item_id = item["kind"], item["id"]
        definition = load(item_path(kind, item_id))
        answer = answers.get(item_id, {})
        weight = float(item.get("weight", 1))
        if kind == "question":
            correct = answer.get("answer") == definition.get("correct_answer")
            score = 100.0 if correct else 0.0
            if definition.get("critical") and not correct:
                critical_errors += 1
        elif kind == "case":
            score = float(answer.get("expert_score", 0))
            if not 0 <= score <= 100:
                raise ValueError(f"Балл кейса {item_id} должен быть от 0 до 100")
        else:
            raise ValueError(f"Неизвестный вид задания: {kind}")
        weighted_score += score * weight
        total_weight += weight
        details.append({"id": item_id, "kind": kind, "score": score, "weight": weight})
    score = round(weighted_score / total_weight, 1) if total_weight else 0.0
    passed = score >= float(assignment.get("pass_score", 80)) and critical_errors <= int(assignment.get("critical_errors_allowed", 0))
    report = {
        "assessment_id": assignment.get("id"),
        "employee_id": assignment.get("employee_id"),
        "role": assignment.get("role"),
        "assessed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "score": score,
        "critical_errors": critical_errors,
        "result": "passed" if passed else "failed",
        "items": details,
    }
    output = ROOT / "training" / "results" / f"{assignment.get('id', 'assessment')}-result.yaml"
    output.write_text(yaml.safe_dump(report, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"Результат: {report['result']}, балл {score}. Отчёт: {output.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
