#!/usr/bin/env bash
# Explicit installer: running this script authorizes the downloads below.
# Normal app startup never calls this script or downloads a native model.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
PYTHON="$REPO_ROOT/app/backend/.venv/bin/python"
if [ "$(uname -s)" != Darwin ] || [ "$(uname -m)" != arm64 ]; then
    echo "This installer requires an Apple Silicon Mac." >&2
    exit 1
fi
if [ ! -x "$PYTHON" ]; then
    echo "Install the backend virtual environment first." >&2
    exit 1
fi
"$PYTHON" -m pip install cmake
if [ ! -d .runtime/whisper.cpp ]; then
    git clone --depth 1 --branch v1.9.5 https://github.com/ggml-org/whisper.cpp.git .runtime/whisper.cpp
fi
"$PYTHON" -m cmake -S .runtime/whisper.cpp -B .runtime/whisper.cpp/build \
    -DCMAKE_BUILD_TYPE=Release -DGGML_METAL=ON -DGGML_METAL_EMBED_LIBRARY=ON \
    -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_SERVER=ON -DWHISPER_CURL=OFF
"$PYTHON" -m cmake --build .runtime/whisper.cpp/build --target whisper-server -j 3
HF_HOME="$REPO_ROOT/models/huggingface" HF_HUB_OFFLINE=0 HF_HUB_DISABLE_IMPLICIT_TOKEN=1 \
"$PYTHON" - <<'PY'
from pathlib import Path
from huggingface_hub import hf_hub_download
path = hf_hub_download('ggerganov/whisper.cpp', 'ggml-small.bin',
                       local_dir=Path.cwd() / 'models/whisper-cpp', token=False)
print('Local Apple GPU model:', path)
PY
echo 'Apple GPU engine installed. Select whisper.cpp + Apple GPU (Metal) in Settings.'
