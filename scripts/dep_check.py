import importlib
import os

mods = [
    "mlx_qwen3_asr" if __import__("platform").system() == "Darwin" and __import__("platform").machine() == "arm64" else "qwen_asr",
    "sounddevice",
    "soundfile",
    "numpy",
    "aiosqlite",
    "openai",
    "anthropic",
]
for m in mods:
    try:
        importlib.import_module(m)
        print(f"{m} OK")
    except Exception:
        print(f"{m} MISSING")

for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
    print(f"{key} {'SET' if os.environ.get(key) else 'UNSET'}")
