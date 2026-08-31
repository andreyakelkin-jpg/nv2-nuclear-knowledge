#!/usr/bin/env sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

python_executable=${NV2_NUCLEAR_PYTHON:-}
if [ -n "$python_executable" ] && [ ! -x "$python_executable" ]; then
    printf '%s\n' "NV2_NUCLEAR_PYTHON is not executable: $python_executable" >&2
    exit 2
fi

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
if [ -z "$python_executable" ]; then
    printf '%s\n' 'Python 3.10+ was not found. Install Python or set NV2_NUCLEAR_PYTHON.' >&2
    exit 2
fi

export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
exec "$python_executable" "$script_dir/run.py" "$@"
