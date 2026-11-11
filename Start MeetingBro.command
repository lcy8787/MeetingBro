#!/bin/bash
# macOS Finder/Terminal launcher. Never installs packages or downloads models.
set -u
set -o pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PATH="/opt/homebrew/bin:/usr/local/bin:${PATH:-/usr/bin:/bin:/usr/sbin:/sbin}"
CHECK_ONLY="${1:-}"

fail() {
    printf '\n啟動檢查失敗：%s\n' "$1" >&2
    if [ -t 0 ] && [ "$CHECK_ONLY" != "--check" ]; then
        read -r -p "按 Enter 關閉此視窗…" _answer
    fi
    exit 1
}

cd "$REPO_ROOT" || fail "無法開啟專案資料夾。"
[ "$(uname -s)" = Darwin ] || fail "此檔供 macOS 使用；Windows 請用 Start MeetingBro.cmd。"
command -v node >/dev/null 2>&1 || fail "找不到 Node.js。"
command -v npm >/dev/null 2>&1 || fail "找不到 npm。"
[ -x app/backend/.venv/bin/python ] || fail "找不到專案的 Python 虛擬環境。"
[ -f app/frontend/node_modules/electron/path.txt ] || fail "Electron 依賴尚未安裝完成。"
[ -x .runtime/whisper.cpp/build/bin/whisper-server ] || fail "Apple GPU 引擎尚未建置完成。"
[ -f models/whisper-cpp/ggml-small.bin ] || fail "找不到 Whisper small 的本地 GGML 模型。"
QWEN_DIR="models/sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25"
for model_file in conv_frontend.onnx encoder.int8.onnx decoder.int8.onnx tokenizer/vocab.json tokenizer/merges.txt; do
    [ -f "$QWEN_DIR/$model_file" ] || fail "Qwen 模型缺少 $model_file。"
done
app/backend/.venv/bin/python -c 'import sys, importlib.util; assert sys.version_info >= (3, 12); assert all(importlib.util.find_spec(m) is not None for m in ("fastapi", "sherpa_onnx", "sounddevice"))' || fail "Python 環境版本或依賴不完整。"

# All model files stay in this project. Startup must not download resources.
export HF_HOME="$REPO_ROOT/models/huggingface"
export ORT_DISABLE_TELEMETRY=1
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export HF_HUB_DISABLE_TELEMETRY=1

printf 'MeetingBro：啟動檢查通過。\n專案位置：%s\n' "$REPO_ROOT"
if [ "$CHECK_ONLY" = "--check" ]; then
    printf '僅檢查環境；沒有啟動軟件、錄音或下載資源。\n'
    exit 0
fi

printf '\n正在開啟 MeetingBro，桌面程式會管理後端及本地模型。\n請保留此終端視窗；完成後可退出程式或按 Ctrl+C 關閉服務。\n\n'
/bin/bash "$REPO_ROOT/scripts/start.sh"
result=$?
if [ "$result" -ne 0 ]; then
    fail "程式以狀態碼 $result 結束，請查看上方訊息。"
fi
exit "$result"
