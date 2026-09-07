#!/usr/bin/env python3
"""Fail-closed scoring for NV2 training assessments.

``evaluate_assessment`` is deliberately pure so callers can preview a result
without creating a personnel record. The command line wrapper is the only
place that writes a report, and writes it under the configured state root.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from kb_root import resolve_kb_root, resolve_state_root
from index_store import index_path, pin_indexes


QUESTION_TYPES = {"single_choice", "multiple_choice", "short_answer", "document_navigation"}
FORMAL_SOURCE_STAGES = {"expert_reviewed", "approved_for_operational_use"}
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class AssessmentValidationError(ValueError):
    """A deterministic input error that must not be turned into a pass."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def load(path: Path) -> dict[str, Any]:
    """Load one YAML mapping, rejecting ambiguous list/scalar inputs."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as error:
        raise AssessmentValidationError("invalid_yaml", f"Не удалось прочитать YAML {path}: {error}") from error
    if not isinstance(data, dict):
        raise AssessmentValidationError("invalid_mapping", f"YAML должен содержать объект: {path}")
    return data


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AssessmentValidationError("invalid_field", f"Поле {name} должно быть объектом")
    return value


def _nonempty_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AssessmentValidationError("missing_field", f"Поле {name} обязательно и должно быть непустой строкой")
    return value.strip()


def _finite_positive(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise AssessmentValidationError("invalid_weight", f"Поле {name} должно быть конечным числом больше нуля")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise AssessmentValidationError("invalid_weight", f"Поле {name} должно быть конечным числом больше нуля") from error
    if not math.isfinite(number) or number <= 0:
        raise AssessmentValidationError("invalid_weight", f"Поле {name} должно быть конечным числом больше нуля")
    return number


def validate_assignment(assignment: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Validate the part of an assignment that is independent of the KB."""
    _nonempty_string(assignment.get("id"), "assignment.id")
    mode = assignment.get("mode", "formal")
    if mode not in {"formal", "pilot"}:
        raise AssessmentValidationError("invalid_mode", "assignment.mode должен быть formal или pilot")
    items = assignment.get("items")
    if not isinstance(items, list) or not items:
        raise AssessmentValidationError("empty_assignment", "assignment.items должен быть непустым списком")
    validated: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, raw_item in enumerate(items):
        item = dict(_mapping(raw_item, f"assignment.items[{index}]"))
        kind = item.get("kind")
        if kind not in {"question", "case"}:
            raise AssessmentValidationError("unknown_item_kind", f"Неизвестный вид задания: {kind!r}")
        item_id = _nonempty_string(item.get("id"), f"assignment.items[{index}].id")
        key = (kind, item_id)
        if key in seen:
            raise AssessmentValidationError("duplicate_item", f"Повтор задания: {kind}:{item_id}")
        seen.add(key)
        item["weight"] = _finite_positive(item.get("weight", 1), f"assignment.items[{index}].weight")
        validated.append(item)
    _finite_positive(assignment.get("pass_score", 80), "assignment.pass_score")
    if float(assignment.get("pass_score", 80)) > 100:
        raise AssessmentValidationError("invalid_pass_score", "assignment.pass_score не может быть больше 100")
    allowed = assignment.get("critical_errors_allowed", 0)
    if isinstance(allowed, bool) or not isinstance(allowed, int) or allowed != 0:
        raise AssessmentValidationError(
            "deprecated_critical_limit",
            "critical_errors_allowed устарел и может быть только 0: критическая ошибка всегда исключает допуск",
        )
    return validated


def _source_references(definition: Mapping[str, Any], label: str) -> list[dict[str, Any]]:
    raw_source = definition.get("source")
    sources = raw_source if isinstance(raw_source, list) else [raw_source]
    if not sources or sources == [None]:
        raise AssessmentValidationError("missing_source", f"У задания {label} отсутствует source")
    result: list[dict[str, Any]] = []
    for position, raw_source_item in enumerate(sources):
        source = dict(_mapping(raw_source_item, f"{label}.source[{position}]"))
        _nonempty_string(source.get("document_id"), f"{label}.source[{position}].document_id")
        result.append(source)
    return result


def _read_definition(root: Path, kind: str, item_id: str) -> dict[str, Any]:
    folder = "question-bank" if kind == "question" else "cases"
    path = root / "training" / folder / f"{item_id}.yaml"
    if not path.is_file():
        raise AssessmentValidationError("missing_item", f"Не найдено определение задания: {kind}:{item_id}")
    definition = load(path)
    if definition.get("id") != item_id:
        raise AssessmentValidationError("item_id_mismatch", f"id в {path} не совпадает с назначением {item_id}")
    _source_references(definition, f"{kind}:{item_id}")
    return definition


def _documents_by_id(root: Path, documents: Any | None) -> dict[str, Mapping[str, Any]]:
    if documents is None:
        documents = load(index_path(root, "meta/documents.yaml")).get("documents", [])
    if isinstance(documents, Mapping):
        documents = documents.get("documents", documents.values())
    if not isinstance(documents, Iterable) or isinstance(documents, (str, bytes)):
        raise AssessmentValidationError("invalid_documents", "Реестр документов должен быть списком")
    index: dict[str, Mapping[str, Any]] = {}
    for document in documents:
        if not isinstance(document, Mapping):
            continue
        document_id = document.get("id")
        if isinstance(document_id, str) and document_id:
            index[document_id] = document
    return index


def _file_hash(root: Path, relative_path: Any) -> str | None:
    if not isinstance(relative_path, str) or not relative_path:
        return None
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    digest = hashlib.sha256()
    with candidate.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _current_source_version(root: Path, declared: Mapping[str, Any], current: Mapping[str, Any] | None) -> dict[str, Any]:
    document_id = str(declared["document_id"])
    if current is None:
        return {"document_id": document_id, "declared": dict(declared), "current": None,
                "changes": ["source_missing"], "formal_qualified": False}
    source_data = current.get("source") if isinstance(current.get("source"), Mapping) else {}
    original_sha = current.get("original_sha256") or current.get("source_sha256") or source_data.get("sha256")
    normalized_path = current.get("normalized_path") or source_data.get("normalized_file")
    normalized_sha = current.get("normalized_sha256") or _file_hash(root, normalized_path)
    lifecycle = current.get("lifecycle_stage")
    if not isinstance(lifecycle, str):
        lifecycle = (current.get("lifecycle") or {}).get("stage") if isinstance(current.get("lifecycle"), Mapping) else None
    formal_qualified = lifecycle in FORMAL_SOURCE_STAGES
    edition = current.get("edition", current.get("revision_date"))
    changes: list[str] = []
    for key, actual in (("edition", edition), ("sha256", original_sha), ("original_sha256", original_sha),
                        ("normalized_sha256", normalized_sha)):
        declared_value = declared.get(key)
        if declared_value is not None:
            if actual is None:
                changes.append(f"{key}_current_missing")
            elif str(declared_value) != str(actual):
                changes.append(f"{key}_changed")
    return {"document_id": document_id, "declared": dict(declared),
            "current": {"edition": edition, "original_sha256": original_sha,
                        "normalized_sha256": normalized_sha, "lifecycle_stage": lifecycle},
            "changes": changes, "formal_qualified": formal_qualified}


def _explicit_exact_answer(definition: Mapping[str, Any]) -> tuple[bool, Any]:
    scoring = definition.get("scoring")
    if isinstance(scoring, Mapping) and "exact_answer" in scoring:
        return True, scoring["exact_answer"]
    if "exact_answer" in definition:
        return True, definition["exact_answer"]
    return False, None


def _score_question(definition: Mapping[str, Any], response: Mapping[str, Any]) -> tuple[float, str, bool]:
    question_type = definition.get("type")
    if question_type not in QUESTION_TYPES:
        raise AssessmentValidationError("invalid_question_type", f"Неизвестный тип вопроса: {question_type!r}")
    has_answer = "answer" in response and response.get("answer") is not None
    if question_type == "single_choice":
        if "correct_answer" not in definition or definition.get("correct_answer") is None:
            return 0.0, "requires_review_missing_correct_answer", False
        expected = definition["correct_answer"]
        non_scalar = (dict, list, tuple, set, frozenset)
        if isinstance(expected, non_scalar):
            return 0.0, "requires_review_invalid_single_choice_correct_answer", False
        if has_answer and isinstance(response["answer"], non_scalar):
            return 0.0, "invalid_single_choice_answer", False
        correct = has_answer and (
            not (isinstance(expected, bool) ^ isinstance(response["answer"], bool))
            and response["answer"] == expected
        )
        return (100.0 if correct else 0.0), ("correct" if correct else "incorrect"), correct
    if question_type == "multiple_choice":
        expected = definition.get("correct_answer")
        if not isinstance(expected, (list, tuple, set, frozenset)) or not expected:
            return 0.0, "requires_review_missing_correct_answer", False
        submitted = response.get("answer")
        if not isinstance(submitted, (list, tuple, set, frozenset)):
            return 0.0, "incorrect", False
        try:
            correct = set(submitted) == set(expected)
        except TypeError:
            return 0.0, "invalid_multiple_choice_answer", False
        return (100.0 if correct else 0.0), ("correct" if correct else "incorrect"), correct
    explicit, expected = _explicit_exact_answer(definition)
    if not explicit:
        return 0.0, "requires_manual_review", False
    correct = has_answer and response["answer"] == expected
    return (100.0 if correct else 0.0), ("correct" if correct else "incorrect"), correct


def _case_critical_failure(response: Mapping[str, Any]) -> bool:
    if response.get("critical_failure") is True:
        return True
    for field in ("critical_errors", "critical_failures"):
        value = response.get(field)
        if isinstance(value, (list, tuple, set, frozenset, dict)) and bool(value):
            return True
        if isinstance(value, str) and value.strip():
            return True
    return False


def _score_case(response: Mapping[str, Any]) -> tuple[float, str, bool]:
    evaluator = response.get("evaluator")
    if not isinstance(evaluator, str) or not evaluator.strip():
        return 0.0, "requires_review_missing_evaluator", False
    score = response.get("expert_score")
    if isinstance(score, bool):
        return 0.0, "requires_review_missing_expert_score", False
    try:
        score = float(score)
    except (TypeError, ValueError):
        return 0.0, "requires_review_missing_expert_score", False
    if not math.isfinite(score) or not 0 <= score <= 100:
        raise AssessmentValidationError("invalid_case_score", "Балл кейса должен быть конечным числом от 0 до 100")
    return score, "expert_scored", True


def evaluate_assessment(assignment: Mapping[str, Any], answers: Mapping[str, Any], root: Path,
                        documents: Any | None = None) -> dict[str, Any]:
    """Return a report without writing files or changing KB state."""
    assignment = _mapping(assignment, "assignment")
    answers = _mapping(answers, "answers")
    items = validate_assignment(assignment)
    answer_values = _mapping(answers.get("answers", answers), "answers.answers")
    document_index = _documents_by_id(root, documents)
    mode = assignment.get("mode", "formal")
    weighted_score = total_weight = 0.0
    critical_error_items: list[str] = []
    fatal_case_items: list[str] = []
    requires_review: list[dict[str, str]] = []
    blockers: list[dict[str, str]] = []
    details: list[dict[str, Any]] = []
    source_versions: list[dict[str, Any]] = []
    version_keys: set[tuple[str, str]] = set()
    for item in items:
        kind, item_id, weight = item["kind"], item["id"], item["weight"]
        definition = _read_definition(root, kind, item_id)
        raw_response = answer_values.get(item_id, {})
        response = _mapping({} if raw_response is None else raw_response, f"answers.{item_id}")
        for declared in _source_references(definition, f"{kind}:{item_id}"):
            version = _current_source_version(root, declared, document_index.get(str(declared["document_id"])))
            identity = (version["document_id"], json.dumps(version["declared"], ensure_ascii=False, sort_keys=True))
            if identity not in version_keys:
                source_versions.append(version)
                version_keys.add(identity)
            if version["changes"]:
                requires_review.append({"item_id": item_id, "reason": ",".join(version["changes"])})
            if mode == "formal" and not any(
                isinstance(declared.get(key), str) and declared[key].strip()
                for key in ("sha256", "original_sha256", "normalized_sha256")
            ):
                requires_review.append({"item_id": item_id, "reason": "source_version_unpinned"})
            if not version["formal_qualified"]:
                entry = {"item_id": item_id, "reason": "source_not_formally_qualified"}
                (blockers if mode == "formal" else requires_review).append(entry)
        if kind == "question":
            score, status, objectively_scored = _score_question(definition, response)
            if status.startswith("requires_"):
                requires_review.append({"item_id": item_id, "reason": status})
            critical_failure = bool(definition.get("critical")) and not objectively_scored
        else:
            score, status, evaluated = _score_case(response)
            if status.startswith("requires_"):
                requires_review.append({"item_id": item_id, "reason": status})
            critical_failure = _case_critical_failure(response)
            if critical_failure:
                fatal_case_items.append(item_id)
            if not evaluated:
                requires_review.append({"item_id": item_id, "reason": "case_not_evaluated"})
        if critical_failure:
            critical_error_items.append(item_id)
        weighted_score += score * weight
        total_weight += weight
        details.append({"id": item_id, "kind": kind, "type": definition.get("type", "case"), "score": score,
                        "weight": weight, "status": status, "critical_failure": critical_failure,
                        "evaluator": response.get("evaluator") if kind == "case" else None})
    score = round(weighted_score / total_weight, 1)
    # The legacy `critical_errors_allowed` field is validated as 0 only above:
    # any critical error is independently disqualifying, never score-compensable.
    score_result = score >= float(assignment.get("pass_score", 80)) and not critical_error_items and not fatal_case_items
    result = (
        "blocked" if blockers else "failed" if critical_error_items
        else "requires_review" if (requires_review or mode == "pilot")
        else "passed" if score_result else "failed"
    )
    return {"assessment_id": assignment["id"], "employee_id": assignment.get("employee_id"), "role": assignment.get("role"),
            "mode": mode, "assessed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "score": score, "pass_score": float(assignment.get("pass_score", 80)), "critical_errors": len(critical_error_items),
            "critical_error_items": critical_error_items, "fatal_case_items": fatal_case_items, "result": result,
            "passed": result == "passed", "score_would_pass": score_result, "blockers": blockers,
            "requires_review": requires_review, "source_versions": source_versions, "items": details}


def _validated_assignment_path(path: Path, root: Path, assessment_id: str) -> Path:
    resolved, expected_directory = path.resolve(), (root / "training" / "assignments").resolve()
    try:
        resolved.relative_to(expected_directory)
    except ValueError as error:
        raise AssessmentValidationError("invalid_assignment_path", "Назначение должно находиться в training/assignments") from error
    if resolved.suffix.lower() not in {".yaml", ".yml"} or not resolved.is_file():
        raise AssessmentValidationError("invalid_assignment_path", "Файл назначения YAML не найден")
    if not SAFE_ID.fullmatch(assessment_id) or resolved.stem != assessment_id:
        raise AssessmentValidationError("invalid_assignment_id", "id назначения должен быть безопасным и совпадать с именем файла")
    return resolved


def _atomic_write_yaml(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            yaml.safe_dump(dict(data), stream, allow_unicode=True, sort_keys=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("assignment")
    parser.add_argument("answers")
    args = parser.parse_args(argv)
    try:
        root = resolve_kb_root()
        assignment_path, assignment = Path(args.assignment), load(Path(args.assignment))
        _validated_assignment_path(assignment_path, root, _nonempty_string(assignment.get("id"), "assignment.id"))
        # Pin the generated registry while scoring so a concurrent rebuild
        # cannot mix document versions inside a personnel record.
        with pin_indexes(root):
            report = evaluate_assessment(assignment, load(Path(args.answers)), root)
        output = resolve_state_root(root) / "training" / "results" / f"{assignment['id']}-result.yaml"
        _atomic_write_yaml(output, report)
    except AssessmentValidationError as error:
        print(json.dumps({"error": {"code": error.code, "message": str(error)}}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (OSError, RuntimeError, FileNotFoundError) as error:
        print(json.dumps({"error": {"code": "io_or_configuration", "message": str(error)}}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({"result": report["result"], "score": report["score"], "report": str(output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
