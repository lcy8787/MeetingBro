#!/usr/bin/env bash
# Install the optional Qwen3-ASR MLX backend and its local 0.6B model.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${ROOT}/app/backend/.venv/bin/python"
MODEL_DIR="${MEETINGBRO_QWEN3_MLX_MODEL:-${ROOT}/models/Qwen3-ASR-0.6B-4bit}"
MODEL_REPO="moona3k/mlx-qwen3-asr-0.6b-4bit"
MODEL_REVISION="4c59c533f95c84afb796655e814709034f826f04"

if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
  printf '%s\n' 'Qwen3 MLX requires macOS on Apple Silicon. Use a Whisper engine on this platform.' >&2
  exit 1
fi
if [[ ! -x "${PYTHON}" ]]; then
  printf '%s\n' 'MeetingBro backend virtual environment not found. Run scripts/install.sh first.' >&2
  exit 1
fi

"${PYTHON}" -m pip install 'mlx-qwen3-asr==0.4.4' 'huggingface_hub>=0.24'

if [[ -d "${MODEL_DIR}" ]]; then
  if [[ -f "${MODEL_DIR}/config.json" && -f "${MODEL_DIR}/vocab.json" && -f "${MODEL_DIR}/merges.txt" ]] && compgen -G "${MODEL_DIR}/*.safetensors" > /dev/null; then
    printf 'Existing model retained: %s\n' "${MODEL_DIR}"
    exit 0
  fi
  printf 'Model directory already exists but is incomplete: %s\n' "${MODEL_DIR}" >&2
  printf '%s\n' 'Complete the existing download or choose another MEETINGBRO_QWEN3_MLX_MODEL directory; no existing files were replaced.' >&2
  exit 1
fi

mkdir -p "$(dirname "${MODEL_DIR}")"
STAGING="$(mktemp -d "${MODEL_DIR}.partial.XXXXXX")"
printf 'Downloading %s into %s (approximately 538 MB).\n' "${MODEL_REPO}" "${STAGING}"
if ! HF_HUB_DISABLE_TELEMETRY=1 "${PYTHON}" - "${MODEL_REPO}" "${MODEL_REVISION}" "${STAGING}" <<'PY'
import sys
from huggingface_hub import snapshot_download

snapshot_download(repo_id=sys.argv[1], revision=sys.argv[2], local_dir=sys.argv[3])
PY
then
  printf 'Download did not finish; partial files remain in %s.\n' "${STAGING}" >&2
  exit 1
fi
if [[ ! -f "${STAGING}/config.json" || ! -f "${STAGING}/vocab.json" || ! -f "${STAGING}/merges.txt" ]] || ! compgen -G "${STAGING}/*.safetensors" > /dev/null; then
  printf 'Downloaded model files are incomplete: %s\n' "${STAGING}" >&2
  exit 1
fi
if [[ -e "${MODEL_DIR}" ]]; then
  printf 'Target appeared during download; files remain in %s and were not moved.\n' "${STAGING}" >&2
  exit 1
fi
mv "${STAGING}" "${MODEL_DIR}"
printf 'Qwen3 MLX model installed: %s\n' "${MODEL_DIR}"
printf '%s\n' 'Restart the backend. Auto selects Qwen3 MLX when the package and model are available.'
