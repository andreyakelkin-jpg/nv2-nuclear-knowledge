from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
