#!/usr/bin/env python3
"""Read-only installation and knowledge-base diagnostics."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import sys
from importlib import metadata
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = PLUGIN_ROOT / ".codex-plugin" / "plugin.json"
CONFIG_ENVIRONMENT_VARIABLE = "NV2_NUCLEAR_CONFIG_PATH"
KB_ENVIRONMENT_VARIABLE = "NV2_NUCLEAR_KB_ROOT"
DEFAULT_CONFIG = Path.home() / ".codex" / "nv2-nuclear-knowledge.yaml"
REQUIRED_KB_PATHS = (
    Path("docs"),
    Path("raw"),
    Path("normalized"),
    Path("meta/documents.yaml"),
    Path("meta/corpus-manifest.yaml"),
)
DEPENDENCIES = {
    "yaml": "PyYAML",
    "pypdf": "pypdf",
    "docx": "python-docx",
    "pdfplumber": "pdfplumber",
}


def add_check(
    checks: list[dict[str, Any]],
    name: str,
    status: str,
    message: str,
    **details: Any,
) -> None:
    checks.append({"name": name, "status": status, "message": message, **details})


def dependency_version(distribution: str) -> str | None:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return None


def read_configured_root(config_path: Path) -> tuple[Path | None, str | None]:
    from yaml import safe_load

    if not config_path.is_file():
        return None, f"Configuration file was not found: {config_path}"
    try:
        payload = safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception as error:  # pragma: no cover - exact parser messages vary
        return None, f"Configuration cannot be read: {error}"
    value = payload.get("kb_root") if isinstance(payload, dict) else None
    if not value:
        return None, f"kb_root is not set in {config_path}"
    return Path(str(value)).expanduser().resolve(), None


def inspect_kb(root: Path) -> tuple[list[str], list[str], dict[str, Any]]:
    os.environ[KB_ENVIRONMENT_VARIABLE] = str(root)
    scripts_root = str(PLUGIN_ROOT / "scripts")
    if scripts_root not in sys.path:
        sys.path.insert(0, scripts_root)
    from kb import validate_base
    from model_router import routing_status

    errors, warnings = validate_base()
    return errors, warnings, routing_status(root)


def diagnose(*, allow_unconfigured: bool, skip_integrity: bool) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    warnings: list[str] = []

    python_ok = sys.version_info >= (3, 10)
    add_check(
        checks,
        "python",
        "pass" if python_ok else "fail",
        f"Python {platform.python_version()} at {sys.executable}",
        minimum="3.10",
    )

    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        manifest_ok = manifest.get("name") == "nv2-nuclear-knowledge" and (PLUGIN_ROOT / "skills").is_dir()
        add_check(
            checks,
            "plugin-package",
            "pass" if manifest_ok else "fail",
            f"Plugin {manifest.get('name')} {manifest.get('version')}",
            manifest=str(MANIFEST),
        )
    except Exception as error:
        add_check(checks, "plugin-package", "fail", f"Plugin manifest cannot be read: {error}")

    missing_dependencies: list[str] = []
    dependency_details: dict[str, str] = {}
    for module, distribution in DEPENDENCIES.items():
        if importlib.util.find_spec(module) is None:
            missing_dependencies.append(distribution)
        else:
            dependency_details[distribution] = dependency_version(distribution) or "available"
    if missing_dependencies:
        add_check(
            checks,
            "dependencies",
            "fail",
            "Missing Python packages: " + ", ".join(sorted(missing_dependencies)),
            installed=dependency_details,
        )
    else:
        add_check(
            checks,
            "dependencies",
            "pass",
            "All declared Python dependencies are available",
            installed=dependency_details,
        )

    root_value = os.environ.get(KB_ENVIRONMENT_VARIABLE)
    config_path = Path(os.environ.get(CONFIG_ENVIRONMENT_VARIABLE, DEFAULT_CONFIG)).expanduser().resolve()
    root: Path | None = Path(root_value).expanduser().resolve() if root_value else None
    root_error: str | None = None
    if root is None and "PyYAML" not in missing_dependencies:
        root, root_error = read_configured_root(config_path)
    elif root is None:
        root_error = "Knowledge-base configuration cannot be read until PyYAML is installed"

    if root is None:
        status = "warn" if allow_unconfigured else "fail"
        message = root_error or "Knowledge-base path is not configured"
        add_check(checks, "knowledge-base", status, message, config=str(config_path))
        if status == "warn":
            warnings.append(message)
    else:
        missing_paths = [str(item) for item in REQUIRED_KB_PATHS if not (root / item).exists()]
        if not root.is_dir() or missing_paths:
            add_check(
                checks,
                "knowledge-base",
                "fail",
                f"Invalid knowledge-base directory: {root}",
                missing=missing_paths,
            )
        else:
            writable = os.access(root, os.W_OK)
            add_check(
                checks,
                "knowledge-base",
                "pass",
                f"Knowledge base is connected: {root}",
                writable=writable,
                source="environment" if root_value else "config",
                config=str(config_path),
            )
            if not writable:
                warning = "Knowledge base is read-only; search works, but archiving is unavailable"
                warnings.append(warning)
                add_check(checks, "write-access", "warn", warning)

            if not skip_integrity and not missing_dependencies:
                try:
                    integrity_errors, integrity_warnings, routing_payload = inspect_kb(root)
                    add_check(
                        checks,
                        "integrity",
                        "pass" if not integrity_errors else "fail",
                        "Knowledge-base integrity validation passed"
                        if not integrity_errors
                        else "Knowledge-base integrity validation failed",
                        errors=integrity_errors,
                        warnings=integrity_warnings,
                    )
                    enabled = bool(routing_payload.get("effective_enabled"))
                    routing_status = "pass" if enabled else "warn"
                    routing_message = (
                        "Cascaded routing and the non-inferiority gate are enabled"
                        if enabled
                        else "Routing is fail-closed to Sol/high"
                    )
                    add_check(
                        checks,
                        "routing",
                        routing_status,
                        routing_message,
                        routing_status=routing_payload,
                    )
                    if not enabled:
                        warnings.append(routing_message)
                except Exception as error:
                    add_check(
                        checks,
                        "integrity",
                        "fail",
                        f"Knowledge-base diagnostics failed: {error}",
                    )

    failed = [item for item in checks if item["status"] == "fail"]
    return {
        "ok": not failed,
        "platform": platform.system().lower(),
        "plugin_root": str(PLUGIN_ROOT),
        "checks": checks,
        "warnings": warnings,
        "errors": [item["message"] for item in failed],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверить установку плагина НВ2 без изменения данных")
    parser.add_argument(
        "--allow-unconfigured",
        action="store_true",
        help="Не считать отсутствие подключённой базы ошибкой",
    )
    parser.add_argument(
        "--skip-integrity",
        action="store_true",
        help="Не запускать полную проверку целостности и routing-status",
    )
    parser.add_argument("--json", action="store_true", help="Вывести машинно-читаемый JSON")
    args = parser.parse_args()
    result = diagnose(
        allow_unconfigured=args.allow_unconfigured,
        skip_integrity=args.skip_integrity,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for item in result["checks"]:
            marker = {"pass": "OK", "warn": "WARN", "fail": "FAIL"}[item["status"]]
            print(f"[{marker}] {item['name']}: {item['message']}")
        print("Плагин готов к работе." if result["ok"] else "Плагин не готов к работе.")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
