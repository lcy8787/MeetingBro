"""Resolve engine and device together so incompatible settings fail clearly."""
import importlib.util
import logging
import os
import platform
from pathlib import Path

from .faster_whisper_adapter import FasterWhisperAdapter
from .whisper_cpp_adapter import WhisperCppAdapter

logger = logging.getLogger(__name__)


def _mlx_model_path() -> str:
    return os.environ.get("MEETINGBRO_QWEN3_MLX_MODEL", str(Path(__file__).resolve().parents[4] / "models" / "Qwen3-ASR-0.6B-4bit"))


def _mlx_available() -> bool:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        return False
    try:
        model = Path(_mlx_model_path()).expanduser()
        required = ("config.json", "vocab.json", "merges.txt")
        return (model.is_dir() and all((model / name).is_file() for name in required)
                and any(model.glob("*.safetensors"))
                and importlib.util.find_spec("mlx_qwen3_asr") is not None)
    except (ImportError, ValueError):
        return False


def build_formal_asr(hardware):
    engine = os.environ.get("MEETINGBRO_WHISPER_ENGINE", "auto").strip().lower()
    if engine == "auto":
        if _mlx_available():
            engine = "qwen3-mlx"
        else:
            engine = hardware.recommended_whisper_engine
            logger.info("Qwen3 MLX unavailable; auto selected %s", engine)
    if engine == "qwen3-mlx":
        from .qwen3_mlx_adapter import Qwen3MLXAdapter
        return Qwen3MLXAdapter(_mlx_model_path()), engine, "metal", "mlx"
    device = os.environ.get("MEETINGBRO_WHISPER_DEVICE", "auto").strip().lower()
    if device == "auto":
        device = ("metal" if hardware.apple_metal_available else "cpu") if engine == "whisper-cpp" else ("cuda" if hardware.ctranslate2_cuda_available else "cpu")
    size = os.environ.get("MEETINGBRO_WHISPER_SIZE", "auto").strip()
    if size == "auto" or not size:
        size = hardware.recommended_whisper_size
    if engine == "whisper-cpp":
        if device == "metal" and not hardware.apple_metal_available:
            raise ValueError("Apple Metal engine is unavailable. Install the local whisper.cpp engine first.")
        adapter = WhisperCppAdapter(model_size=size, device=device,
                                    cpu_threads=int(os.environ.get("MEETINGBRO_WHISPER_CPU_THREADS", "2")) or 2)
        return adapter, engine, device, "float16"
    if engine != "faster-whisper":
        raise ValueError(f"Unknown Whisper engine: {engine}")
    if device not in {"cpu", "cuda"}:
        raise ValueError("faster-whisper supports CPU/CUDA; select whisper.cpp for Apple Metal.")
    compute = os.environ.get("MEETINGBRO_WHISPER_COMPUTE_TYPE", "auto").strip()
    if compute == "auto" or not compute:
        compute = "int8" if device == "cpu" else "float16"
    adapter = FasterWhisperAdapter(
        model_size=size, device=device, compute_type=compute,
        beam_size=int(os.environ.get("MEETINGBRO_WHISPER_BEAM_SIZE", "1")),
        cpu_threads=int(os.environ.get("MEETINGBRO_WHISPER_CPU_THREADS", "0")),
        num_workers=int(os.environ.get("MEETINGBRO_WHISPER_NUM_WORKERS", "1")),
        vad_threshold=float(os.environ.get("MEETINGBRO_WHISPER_VAD_THRESHOLD", "0.3")),
        vad_min_speech_ms=int(os.environ.get("MEETINGBRO_WHISPER_VAD_MIN_SPEECH_MS", "100")),
        vad_min_silence_ms=int(os.environ.get("MEETINGBRO_WHISPER_VAD_MIN_SILENCE_MS", "300")),
        vad_speech_pad_ms=int(os.environ.get("MEETINGBRO_WHISPER_VAD_SPEECH_PAD_MS", "400")),
        multilingual=os.environ.get("MEETINGBRO_WHISPER_MULTILINGUAL", "false").lower() in {"1", "true", "yes"},
        language_detection_threshold=float(os.environ.get("MEETINGBRO_WHISPER_LANGUAGE_DETECTION_THRESHOLD", "0.5")),
        language_detection_segments=int(os.environ.get("MEETINGBRO_WHISPER_LANGUAGE_DETECTION_SEGMENTS", "1")),
    )
    return adapter, engine, device, compute
