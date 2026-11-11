"""Qwen3-only recognition: one selected model shared by preview and final ASR."""
import os
import platform
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def qwen_model_size() -> str:
    size = os.environ.get("MEETINGBRO_QWEN3_MODEL_SIZE", "0.6B").strip()
    if size not in {"0.6B", "1.7B"}:
        raise ValueError("MEETINGBRO_QWEN3_MODEL_SIZE must be 0.6B or 1.7B")
    return size


def build_formal_asr(hardware):
    size = qwen_model_size()
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        from .qwen3_mlx_adapter import Qwen3MLXAdapter
        path = os.environ.get("MEETINGBRO_QWEN3_MLX_MODEL", str(ROOT / "models" / f"Qwen3-ASR-{size}-4bit"))
        return Qwen3MLXAdapter(path), "qwen3-mlx", "metal", "mlx"
    from .qwen3_native_adapter import Qwen3NativeAdapter
    path = os.environ.get("MEETINGBRO_QWEN3_MODEL", str(ROOT / "models" / f"Qwen3-ASR-{size}"))
    device = "cuda" if hardware.cuda_available else "cpu"
    return Qwen3NativeAdapter(path, device=device), "qwen3", device, "float16" if device == "cuda" else "float32"
