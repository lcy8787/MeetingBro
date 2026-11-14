$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
& "$Root/app/backend/.venv/Scripts/python.exe" "$PSScriptRoot/install_qwen3_gguf.py" @args
exit $LASTEXITCODE
