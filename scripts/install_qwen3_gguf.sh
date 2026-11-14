#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "${ROOT}/app/backend/.venv/bin/python" "${ROOT}/scripts/install_qwen3_gguf.py" "$@"
