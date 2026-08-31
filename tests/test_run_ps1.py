from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
RUN_SCRIPT = PLUGIN_ROOT / "scripts" / "run.ps1"


@unittest.skipUnless(sys.platform == "win32", "PowerShell launcher is Windows-specific")
class PowerShellLauncherTests(unittest.TestCase):
    def test_windows_powershell_launches_from_another_project(self) -> None:
        powershell = shutil.which("powershell.exe")
        if not powershell:
            self.skipTest("Windows PowerShell is not available")

        environment = os.environ.copy()
        environment["NV2_NUCLEAR_PYTHON"] = sys.executable
        environment["PYTHONUTF8"] = "1"

        # Keep the foreign working directory inside the checkout so the test
        # also runs under Codex's Windows sandbox identity.
        with tempfile.TemporaryDirectory(prefix=".other-project-", dir=PLUGIN_ROOT) as other_project:
            completed = subprocess.run(
                [
                    powershell,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(RUN_SCRIPT),
                    "kb",
                    "--help",
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=environment,
                cwd=other_project,
            )

        self.assertEqual(
            0,
            completed.returncode,
            f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}",
        )
        self.assertIn("usage:", completed.stdout.lower())

    def test_windows_installer_configures_another_user_environment(self) -> None:
        powershell = shutil.which("powershell.exe")
        if not powershell:
            self.skipTest("Windows PowerShell is not available")

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
                    powershell,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(PLUGIN_ROOT / "scripts" / "install.ps1"),
                    "-KbRoot",
                    str(root),
                    "-Python",
                    sys.executable,
                    "-SkipDependencies",
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
