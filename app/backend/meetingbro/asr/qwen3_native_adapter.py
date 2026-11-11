"""Local Qwen3-ASR transformers backend for Windows/Linux and Intel Macs."""
from pathlib import Path
import threading
import numpy as np
from .base import ASRAdapter, ASRSegment
from ..text_locale import to_traditional


class Qwen3NativeAdapter(ASRAdapter):
    def __init__(self, model_path: str, *, device: str = "cpu"):
        self.model_path = Path(model_path).expanduser()
        self.device = device
        self._model = None
        self._lock = threading.RLock()

    def ensure_loaded(self):
        with self._lock:
            if self._model is not None:
                return
            if not (self.model_path / "config.json").is_file() or not any(self.model_path.glob("*.safetensors")):
                raise RuntimeError(f"Install the complete local Qwen3-ASR model at {self.model_path}")
            try:
                import torch
                from qwen_asr import Qwen3ASRModel
            except ImportError as exc:
                raise RuntimeError("Install the Qwen backend: pip install -e '.[qwen]'") from exc
            self._model = Qwen3ASRModel.from_pretrained(str(self.model_path),
                dtype=torch.float16 if self.device == "cuda" else torch.float32,
                device_map=self.device, local_files_only=True,
                max_inference_batch_size=1, max_new_tokens=512)

    def close(self):
        with self._lock:
            self._model = None

    def transcribe(self, samples, sample_rate, *, forced_language=None, offset_seconds=0., initial_prompt=None, quality_preset="realtime"):
        if not samples.size:
            return []
        if sample_rate != 16000 or samples.ndim != 1:
            raise ValueError("Qwen3 expects mono 16 kHz audio")
        with self._lock:
            self.ensure_loaded()
            results = self._model.transcribe(audio=(np.asarray(samples, dtype=np.float32), sample_rate),
                language={"zh":"Chinese", "en":"English", "de":"German"}.get(forced_language),
                context=initial_prompt or "")
        result = results[0]
        text = to_traditional(result.text.strip())
        language = {"chinese":"zh", "cantonese":"zh", "english":"en", "german":"de"}.get(result.language.lower(), "unknown")
        return [ASRSegment(start_time=offset_seconds, end_time=offset_seconds + len(samples)/sample_rate,
                           text=text, language=language, confidence=.8)] if text else []
