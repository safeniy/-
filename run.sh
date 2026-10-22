#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ -x .venv/bin/python ]; then
    NOTEBOOK_INTERPRETER=.venv/bin/python
elif [ -x /workspace/handwriting-venv/bin/python ]; then
    NOTEBOOK_INTERPRETER=/workspace/handwriting-venv/bin/python
else
    echo 'Создайте .venv и установите зависимости по README.md' >&2
    exit 1
fi
exec "$NOTEBOOK_INTERPRETER" run.py "$@"
