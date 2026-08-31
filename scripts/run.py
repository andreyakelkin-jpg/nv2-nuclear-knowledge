#!/usr/bin/env python3
"""Cross-platform entry point for NV2 nuclear knowledge commands."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


SCRIPTS_ROOT = Path(__file__).resolve().parent
ACTIONS = {
    "kb": SCRIPTS_ROOT / "kb.py",
    "assess": SCRIPTS_ROOT / "assess_training.py",
    "configure": SCRIPTS_ROOT / "configure.py",
    "doctor": SCRIPTS_ROOT / "doctor.py",
}


def usage() -> str:
    return (
        "usage: run.py <kb|assess|configure|doctor> [arguments...]\n"
        "Run with the same Python 3.10+ interpreter on Windows or Linux."
    )


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] in {"-h", "--help"}:
        print(usage())
        return 0

    action = arguments.pop(0)
    target = ACTIONS.get(action)
    if target is None:
        print(f"Unknown action: {action}\n{usage()}", file=sys.stderr)
        return 2
    if not target.is_file():
        print(f"Command entry point is missing: {target}", file=sys.stderr)
        return 2

    environment = os.environ.copy()
    environment.setdefault("PYTHONUTF8", "1")
    environment.setdefault("PYTHONIOENCODING", "utf-8")
    completed = subprocess.run(
        [sys.executable, str(target), *arguments],
        check=False,
        env=environment,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
