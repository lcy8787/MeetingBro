"""Optional Qwen3-ASR MLX formal backend, using an explicitly local model."""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np

from .base import ASRAdapter, ASRSegment
from ..text_locale import to_traditional

logger = logging.getLogger(__name__)


class Qwen3MLXAdapter(ASRAdapter):
    def __init__(self, model_path: str):
        self.model_path = Path(model_path).expanduser()
        self._session = None
        self._lock = threading.Lock()

    def ensure_loaded(self) -> None:
        with self._lock:
            if self._session is not None:
                return
            if not self.model_path.is_dir():
                raise RuntimeError(f"Qwen3 MLX model directory not found: {self.model_path}. Install a local model or select Whisper.")
            required = ("config.json", "vocab.json", "merges.txt")
            missing = [name for name in required if not (self.model_path / name).is_file()]
            if not any(self.model_path.glob("*.safetensors")):
                missing.append("*.safetensors")
            if missing:
                raise RuntimeError(f"Qwen3 MLX model download is incomplete ({', '.join(missing)}). Complete the local download or select Whisper.")
            try:
                from mlx_qwen3_asr import Session
            except ImportError as exc:
                raise RuntimeError("Qwen3 MLX requires mlx-qwen3-asr 0.4.4. Install the optional dependency or select Whisper.") from exc
            try:
                self._session = Session(model=str(self.model_path))
            except Exception as exc:
                raise RuntimeError(f"Could not load Qwen3 MLX model: {exc}. Check memory or select Whisper.") from exc

    def close(self) -> None:
        with self._lock:
            self._session = None

    def transcribe(self, samples: np.ndarray, sample_rate: int, *, forced_language: Optional[str] = None,
                   offset_seconds: float = 0.0, initial_prompt: Optional[str] = None,
                   quality_preset: str = "realtime") -> list[ASRSegment]:
        if not samples.size:
            return []
        if sample_rate != 16000 or samples.ndim != 1:
            raise ValueError("Qwen3 MLX expects mono 16 kHz audio")
        self.ensure_loaded()
        # Cantonese is not assumed to be an accepted forced-language token.
        # Auto detection preserves the model's Cantonese output where supported.
        language = {"zh": "Chinese", "en": "English", "de": "German"}.get(forced_language)
        started = time.monotonic()
        with self._lock:
            result = self._session.transcribe(np.asarray(samples, dtype=np.float32), language=language,
                                              context=initial_prompt or "")
        logger.info("qwen3_mlx inference audio=%.2fs wall=%.2fs", len(samples) / sample_rate, time.monotonic() - started)
        text = to_traditional(result.text.strip())
        if not text:
            return []
        detected = (getattr(result, "language", "") or "").lower()
        original = {"chinese": "zh", "cantonese": "zh", "english": "en", "german": "de"}.get(detected, "unknown")
        if getattr(result, "truncated", False):
            logger.warning("Qwen3 MLX result reached its token limit")
        return [ASRSegment(start_time=offset_seconds, end_time=offset_seconds + len(samples) / sample_rate,
                           text=text, language=original, confidence=0.8)]
