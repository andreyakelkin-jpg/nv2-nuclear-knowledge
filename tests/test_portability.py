from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "scripts"
RUNNER = SCRIPTS / "run.py"
KB_SCRIPT = SCRIPTS / "kb.py"


class PortableLauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        for directory in ("docs", "raw", "normalized", "meta", "reports"):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        (self.root / "meta/documents.yaml").write_text("documents: []\n", encoding="utf-8")
        (self.root / "meta/categories.yaml").write_text("categories: []\n", encoding="utf-8")
        (self.root / "meta/corpus-manifest.yaml").write_text("schema_version: 2\n", encoding="utf-8")
        self.config = self.root / "config.yaml"
        self.config.write_text(
            yaml.safe_dump({"kb_root": str(self.root)}, allow_unicode=True),
            encoding="utf-8",
        )
        self.environment = os.environ.copy()
        self.environment.update(
            {
                "NV2_NUCLEAR_KB_ROOT": str(self.root),
                "NV2_NUCLEAR_CONFIG_PATH": str(self.config),
                "NV2_NUCLEAR_PYTHON": sys.executable,
                "PYTHONUTF8": "1",
            }
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_command(self, command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=self.environment,
            cwd=cwd,
        )

    def test_portable_runner_matches_direct_kb_output(self) -> None:
        direct = self.run_command([sys.executable, str(KB_SCRIPT), "root"])
        portable = self.run_command([sys.executable, str(RUNNER), "kb", "root"])
        self.assertEqual(0, direct.returncode, direct.stderr)
        self.assertEqual(0, portable.returncode, portable.stderr)
        self.assertEqual(direct.stdout, portable.stdout)

    def test_portable_runner_works_from_another_project(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nv2-other-project-") as other_project:
            completed = self.run_command(
                [sys.executable, str(RUNNER), "kb", "--help"],
                cwd=Path(other_project),
            )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("usage:", completed.stdout.lower())

    def test_doctor_accepts_valid_cross_platform_configuration(self) -> None:
        completed = self.run_command(
            [sys.executable, str(RUNNER), "doctor", "--skip-integrity", "--json"]
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertTrue(result["ok"])
        statuses = {item["name"]: item["status"] for item in result["checks"]}
        self.assertEqual("pass", statuses["plugin-package"])
        self.assertEqual("pass", statuses["dependencies"])
        self.assertEqual("pass", statuses["knowledge-base"])

    def test_doctor_full_check_is_read_only(self) -> None:
        reports_before = sorted(path.relative_to(self.root) for path in self.root.rglob("*") if path.is_file())
        completed = self.run_command([sys.executable, str(RUNNER), "doctor", "--json"])
        reports_after = sorted(path.relative_to(self.root) for path in self.root.rglob("*") if path.is_file())
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertTrue(json.loads(completed.stdout)["ok"])
        self.assertEqual(reports_before, reports_after)


@unittest.skipIf(sys.platform == "win32", "POSIX wrapper is tested on Linux CI")
class PosixLauncherTests(unittest.TestCase):
    def test_posix_wrapper_launches_portable_runner(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nv2-wrapper-") as temporary:
            root = Path(temporary) / "knowledge-base"
            for directory in ("docs", "raw", "normalized", "meta", "reports"):
                (root / directory).mkdir(parents=True, exist_ok=True)
            (root / "meta/documents.yaml").write_text("documents: []\n", encoding="utf-8")
            (root / "meta/categories.yaml").write_text("categories: []\n", encoding="utf-8")
            (root / "meta/corpus-manifest.yaml").write_text("schema_version: 2\n", encoding="utf-8")
            environment = os.environ.copy()
            environment.update(
                {
                    "NV2_NUCLEAR_KB_ROOT": str(root),
                    "NV2_NUCLEAR_PYTHON": sys.executable,
                    "PYTHONUTF8": "1",
                }
            )
            completed = subprocess.run(
                ["sh", str(SCRIPTS / "run.sh"), "kb", "--help"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=environment,
            )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("usage:", completed.stdout.lower())

    def test_posix_installer_configures_another_user_environment(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nv2-install-") as temporary:
            root = Path(temporary) / "knowledge-base"
            for directory in ("docs", "raw", "normalized", "meta", "reports"):
                (root / directory).mkdir(parents=True, exist_ok=True)
            (root / "meta/documents.yaml").write_text("documents: []\n", encoding="utf-8")
            (root / "meta/categories.yaml").write_text("categories: []\n", encoding="utf-8")
            (root / "meta/corpus-manifest.yaml").write_text("schema_version: 2\n", encoding="utf-8")
            config = Path(temporary) / "user-config.yaml"
            environment = os.environ.copy()
            environment.update(
                {
                    "NV2_NUCLEAR_CONFIG_PATH": str(config),
                    "NV2_NUCLEAR_PYTHON": sys.executable,
                    "PYTHONUTF8": "1",
                }
            )
            completed = subprocess.run(
                [
                    "sh",
                    str(SCRIPTS / "install.sh"),
                    "--kb-root",
                    str(root),
                    "--python",
                    sys.executable,
                    "--skip-dependencies",
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=environment,
            )
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            self.assertTrue(config.is_file())
            configured_root = Path(yaml.safe_load(config.read_text(encoding="utf-8"))["kb_root"])
            self.assertTrue(configured_root.samefile(root))


if __name__ == "__main__":
    unittest.main()
