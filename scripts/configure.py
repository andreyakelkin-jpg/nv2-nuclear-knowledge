#!/usr/bin/env python3
"""Configure the external normative knowledge-base directory for the plugin."""
from __future__ import annotations

import argparse

from kb_root import write_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Подключить нормативную базу к плагину НВ2")
    parser.add_argument("kb_root", help="Путь к каталогу нормативной базы")
    args = parser.parse_args()
    config_path = write_config(args.kb_root)
    print(f"Конфигурация сохранена: {config_path}")


if __name__ == "__main__":
    main()
