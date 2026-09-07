from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import answer_service  # noqa: E402
import kb_root  # noqa: E402
from document_usage import document_usage_statistics  # noqa: E402
from model_router import start_route  # noqa: E402


class FinishAnswerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        temporary_root = Path(self.temporary.name)
        self.root = temporary_root / "knowledge-base"
        self.state = temporary_root / "runtime-state"
        self.config = temporary_root / "config.yaml"
        for directory in ("docs", "raw", "normalized", "meta", "reports"):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        self.state.mkdir()
        self._write_yaml(self.root / "meta/documents.yaml", {"documents": []})
        self._write_yaml(self.root / "meta/corpus-manifest.yaml", {"schema_version": 2})
        self._write_yaml(self.config, {
            "kb_root": str(self.root), "state_root": str(self.state),
            "routing": {"enabled": False, "require_quality_gate": True},
        })
        self.environment = {
            "NV2_NUCLEAR_KB_ROOT": str(self.root),
            "NV2_NUCLEAR_STATE_ROOT": str(self.state),
            "NV2_NUCLEAR_CONFIG_PATH": str(self.config),
        }
        self.config_patch = patch.object(kb_root, "CONFIG_PATH", self.config)
        self.config_patch.start()
        self.environment_patch = patch.dict(os.environ, self.environment, clear=False)
        self.environment_patch.start()
        self.answer = self.root / "answer.txt"
        self.answer.write_text("Принятый ответ.", encoding="utf-8")
        self.contract = self.root / "contract.yaml"
        self._write_yaml(self.contract, {"min_chars": 1})

    def tearDown(self) -> None:
        self.environment_patch.stop()
        self.config_patch.stop()
        self.temporary.cleanup()

    @staticmethod
    def _write_yaml(path: Path, data: dict) -> None:
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    def _run(self) -> str:
        return start_route(
            self.root, task_fingerprint="answer-service-test",
            complexity="low", ambiguity="low", criticality="low", context_chars=10,
            tool_count=0, side_effects="none", confidence=0.99,
        )["run_id"]

    def _finish(self, run_id: str, **kwargs: object) -> dict:
        return answer_service.finish_answer(
            self.root, run_id, answer_path=self.answer, contract_path=self.contract,
            used=list(kwargs.pop("used", ["ГОСТ 9.999-2024"])),
            missing=list(kwargs.pop("missing", [])), **kwargs,
        )

    def _usage_count(self) -> int:
        stats = document_usage_statistics(self.state, self.root)
        return stats["documents"][0]["used_count"] if stats["documents"] else 0

    def test_accepted_finish_and_unchanged_replay_count_once(self) -> None:
        run_id = self._run()
        first = self._finish(run_id)
        replay = self._finish(run_id)
        self.assertTrue(first["accepted"])
        self.assertFalse(first.get("replayed", False))
        self.assertTrue(replay["replayed"])
        self.assertEqual(1, first["usage"]["counter_increments"])
        self.assertEqual(0, replay["usage"]["counter_increments"])
        self.assertEqual(1, self._usage_count())

    def test_changed_finish_payload_is_rejected_after_completion(self) -> None:
        run_id = self._run()
        self._finish(run_id)
        with self.assertRaisesRegex(ValueError, "cannot be changed"):
            self._finish(run_id, used=["ГОСТ 1.111-2025"])
        self.assertEqual(1, self._usage_count())

    def test_failed_validation_does_not_record_usage(self) -> None:
        run_id = self._run()
        self.answer.write_text("x", encoding="utf-8")
        self._write_yaml(self.contract, {"min_chars": 100})
        result = self._finish(run_id)
        self.assertFalse(result["accepted"])
        self.assertNotIn("usage", result)
        self.assertEqual(0, self._usage_count())

    def test_stats_failure_recovery_replays_without_double_count(self) -> None:
        run_id = self._run()
        real_record = answer_service.record_document_usage
        attempts = 0

        def fail_once(*args: object, **kwargs: object) -> dict:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("temporary stats failure")
            return real_record(*args, **kwargs)

        with patch.object(
            answer_service,
            "record_document_usage",
            side_effect=fail_once,
        ) as record:
            with self.assertRaisesRegex(RuntimeError, "temporary stats failure"):
                self._finish(run_id)
            recovered = self._finish(run_id)
        self.assertTrue(recovered["accepted"])
        self.assertTrue(recovered["replayed"])
        self.assertEqual(2, record.call_count)
        self.assertEqual(1, self._usage_count())

    def test_unknown_input_tokens_remain_none(self) -> None:
        result = self._finish(self._run(), input_tokens=None, output_tokens=7)
        self.assertIsNone(result["tokens"]["input"])
        self.assertEqual(7, result["tokens"]["output"])
        self.assertIsNone(result["tokens"]["total"])
        self.assertEqual("partial", result["tokens"]["source"])

    def test_negative_measurements_are_rejected_before_usage(self) -> None:
        run_id = self._run()
        with self.assertRaisesRegex(ValueError, "nonnegative"):
            self._finish(run_id, input_tokens=-1)
        self.assertEqual(0, self._usage_count())


if __name__ == "__main__":
    unittest.main()
