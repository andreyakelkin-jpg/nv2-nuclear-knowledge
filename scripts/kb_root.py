#!/usr/bin/env python3
"""Resolve the external NV2 normative knowledge-base directory."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


ENVIRONMENT_VARIABLE = "NV2_NUCLEAR_KB_ROOT"
CONFIG_ENVIRONMENT_VARIABLE = "NV2_NUCLEAR_CONFIG_PATH"
CONFIG_PATH = Path(os.environ.get(CONFIG_ENVIRONMENT_VARIABLE, Path.home() / ".codex" / "nv2-nuclear-knowledge.yaml"))
SUPPORTED_SCHEMA_VERSION = 2
REQUIRED_PATHS = (
    Path("docs"),
    Path("raw"),
    Path("normalized"),
    Path("meta/documents.yaml"),
    Path("meta/corpus-manifest.yaml"),
)


def read_config() -> dict[str, Any]:
    if not CONFIG_PATH.is_file():
        return {}
    loaded = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"Конфигурация должна быть YAML-объектом: {CONFIG_PATH}")
    return loaded


def validate_kb_root(value: str | Path) -> Path:
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Каталог нормативной базы не найден: {root}")
    missing = [str(path) for path in REQUIRED_PATHS if not (root / path).exists()]
    if missing:
        raise FileNotFoundError(
            f"Каталог не является нормативной базой НВ2: {root}. Отсутствует: {', '.join(missing)}"
        )
    manifest = yaml.safe_load((root / "meta/corpus-manifest.yaml").read_text(encoding="utf-8")) or {}
    schema_version = manifest.get("schema_version")
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        raise RuntimeError(
            f"Несовместимая версия схемы базы: {schema_version}; "
            f"плагин поддерживает {SUPPORTED_SCHEMA_VERSION}"
        )
    return root


def resolve_kb_root() -> Path:
    configured = os.environ.get(ENVIRONMENT_VARIABLE)
    if not configured:
        configured = read_config().get("kb_root")
    if not configured:
        raise FileNotFoundError(
            f"Нормативная база не подключена. Укажите {ENVIRONMENT_VARIABLE} или создайте {CONFIG_PATH}"
        )
    return validate_kb_root(str(configured))


def write_config(root: str | Path) -> Path:
    validated = validate_kb_root(root)
    config = read_config()
    config["kb_root"] = str(validated)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return CONFIG_PATH


def update_config(values: dict[str, Any]) -> Path:
    config = read_config()
    config.update(values)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return CONFIG_PATH
