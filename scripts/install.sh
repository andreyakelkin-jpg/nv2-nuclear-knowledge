#!/usr/bin/env sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
kb_root=
skip_dependencies=0
python_executable=${NV2_NUCLEAR_PYTHON:-}

usage() {
    printf '%s\n' 'usage: install.sh [--kb-root PATH] [--python PATH] [--skip-dependencies]'
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --kb-root)
            [ "$#" -ge 2 ] || { usage >&2; exit 2; }
            kb_root=$2
            shift 2
            ;;
        --python)
            [ "$#" -ge 2 ] || { usage >&2; exit 2; }
            python_executable=$2
            shift 2
            ;;
        --skip-dependencies)
            skip_dependencies=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            printf '%s\n' "Unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [ -z "$python_executable" ]; then
    for candidate in \
        "${HOME:-}/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3" \
        "${HOME:-}/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python"
    do
        if [ -n "${HOME:-}" ] && [ -x "$candidate" ]; then
            python_executable=$candidate
            break
        fi
    done
fi
if [ -z "$python_executable" ] && command -v python3 >/dev/null 2>&1; then
    python_executable=$(command -v python3)
fi
if [ -z "$python_executable" ] && command -v python >/dev/null 2>&1; then
    python_executable=$(command -v python)
fi
if [ -z "$python_executable" ] || [ ! -x "$python_executable" ]; then
    printf '%s\n' 'Python 3.10+ was not found. Pass --python or set NV2_NUCLEAR_PYTHON.' >&2
    exit 2
fi

export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
if [ "$skip_dependencies" -eq 0 ]; then
    "$python_executable" -m pip install --disable-pip-version-check -r "$script_dir/requirements.txt"
fi

if [ -n "$kb_root" ]; then
    "$python_executable" "$script_dir/run.py" configure "$kb_root"
    exec "$python_executable" "$script_dir/run.py" doctor
fi

"$python_executable" "$script_dir/run.py" doctor --allow-unconfigured --skip-integrity
printf '%s\n' 'The plugin is installed, but the knowledge-base path is not configured.' >&2
