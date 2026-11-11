"""Persistent, local whisper.cpp engine with verified Apple Metal acceleration.

The native model stays loaded in a child process. No model download is ever
initiated here; installation is an explicit, separate operation.
"""
from __future__ import annotations

import atexit
import io
import json
import logging
import math
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
import wave

import numpy as np

from .base import ASRAdapter, ASRSegment
from .streaming import Word

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[4]


def engine_binary() -> Path:
    return Path(os.environ.get("MEETINGBRO_WHISPER_CPP_BINARY") or
                PROJECT_ROOT / ".runtime/whisper.cpp/build/bin/whisper-server").expanduser()


def model_path(size: str) -> Path:
    override = os.environ.get("MEETINGBRO_WHISPER_CPP_MODEL", "").strip()
    return Path(override).expanduser() if override else PROJECT_ROOT / "models/whisper-cpp" / f"ggml-{size}.bin"


def _multipart(fields: dict[str, str], audio: bytes) -> tuple[bytes, str]:
    boundary = "meetingbro-" + uuid.uuid4().hex
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    chunks.extend([
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode(),
        audio, f'\r\n--{boundary}--\r\n'.encode(),
    ])
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


class WhisperCppAdapter(ASRAdapter):
    def __init__(self, *, model_size: str = "small", device: str = "metal", cpu_threads: int = 2):
        if device not in {"metal", "cpu"}:
            raise ValueError("whisper.cpp device must be metal or cpu")
        self._device = device
        self._model_size = model_size
        self._threads = max(1, cpu_threads)
        self._process: subprocess.Popen | None = None
        self._lock = threading.RLock()
        self._log_tail: list[str] = []
        self._metal_active = False
        self._base = ""
        self._http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        atexit.register(self.close)

    def _read_logs(self, pipe) -> None:
        try:
            for line in pipe:
                line = line.rstrip()
                self._log_tail.append(line)
                self._log_tail = self._log_tail[-100:]
                if ("using Metal" in line or "using MTL" in line) and "backend" in line:
                    self._metal_active = True
                if "failed" in line.lower() and ("metal" in line.lower() or "MTL" in line):
                    self._metal_active = False
                # Keep native diagnostics, without logging recognized audio.
                if "metal" in line.lower() or "MTL" in line or "error" in line.lower():
                    logger.info("whisper.cpp: %s", line)
        finally:
            pipe.close()

    def ensure_loaded(self) -> None:
        with self._lock:
            if self._process is not None:
                if self._process.poll() is None:
                    return
                raise RuntimeError("whisper.cpp stopped: " + "\n".join(self._log_tail[-8:]))
            binary, model = engine_binary(), model_path(self._model_size)
            if not binary.is_file():
                raise RuntimeError(f"Apple GPU engine is not installed at {binary}. Run scripts/install_apple_gpu.sh after approving its downloads.")
            if not model.is_file():
                raise RuntimeError(f"Local whisper.cpp model missing: {model}. Model downloads require approval.")
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            self._base = f"http://127.0.0.1:{port}"
            work = PROJECT_ROOT / ".runtime/whisper-service"
            work.mkdir(parents=True, exist_ok=True)
            command = [str(binary.resolve()), "-m", str(model.resolve()), "--host", "127.0.0.1",
                       "--port", str(port), "-t", str(self._threads), "-l", "auto",
                       "-bo", "1", "-bs", "1", "-nf"]
            if self._device == "cpu":
                command.append("-ng")
            self._metal_active = False
            self._log_tail = []
            self._process = subprocess.Popen(command, cwd=work, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.PIPE, text=True)
            threading.Thread(target=self._read_logs, args=(self._process.stderr,), daemon=True).start()
            deadline = time.monotonic() + 120
            try:
                while time.monotonic() < deadline:
                    if self._process.poll() is not None:
                        raise RuntimeError("Native engine exited: " + "\n".join(self._log_tail[-12:]))
                    try:
                        with self._http.open(self._base + "/health", timeout=1) as response:
                            ready = json.load(response).get("status") == "ok"
                        if ready:
                            if self._device == "metal" and not self._metal_active:
                                raise RuntimeError("Native engine loaded without a confirmed Metal compute backend; refusing to report GPU acceleration.")
                            logger.info("whisper.cpp ready: model=%s device=%s threads=%d", self._model_size, self._device, self._threads)
                            return
                    except (urllib.error.URLError, TimeoutError):
                        pass
                    time.sleep(0.1)
                raise RuntimeError("Timed out loading whisper.cpp: " + "\n".join(self._log_tail[-8:]))
            except BaseException:
                self.close()
                raise

    def close(self) -> None:
        with self._lock:
            process, self._process = self._process, None
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

    def _decode(self, samples: np.ndarray, sample_rate: int, *, language: str | None,
                prompt: str | None, words: bool = False) -> dict:
        if sample_rate != 16000:
            raise ValueError("WhisperCppAdapter expects 16 kHz mono audio")
        if samples.ndim != 1 or not np.isfinite(samples).all():
            raise ValueError("Expected finite mono audio")
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
        fields = {"response_format": "verbose_json", "language": language or "auto",
                  "temperature": "0", "temperature_inc": "0", "no_context": "true",
                  "no_language_probabilities": "true",
                  "token_timestamps": "true" if words else "false", "max_len": "0",
                  "prompt": prompt or ""}
        payload, content_type = _multipart(fields, buffer.getvalue())
        with self._lock:
            self.ensure_loaded()
            request = urllib.request.Request(self._base + "/inference", data=payload,
                                             headers={"Content-Type": content_type})
            try:
                with self._http.open(request, timeout=120) as response:
                    return json.load(response)
            except urllib.error.HTTPError as exc:
                raise RuntimeError(f"whisper.cpp inference failed (HTTP {exc.code})") from exc

    def transcribe(self, samples: np.ndarray, sample_rate: int, *, forced_language=None,
                   offset_seconds: float = 0.0, initial_prompt=None, quality_preset="realtime") -> list[ASRSegment]:
        if samples.size == 0:
            return []
        result = self._decode(samples, sample_rate, language=forced_language, prompt=initial_prompt)
        duration = len(samples) / sample_rate
        language = result.get("language", forced_language or "unknown")
        language = {"english": "en", "chinese": "zh", "german": "de"}.get(language, language)
        if language not in {"zh", "en", "de"}:
            language = "unknown"
        segments = []
        for item in result.get("segments", []):
            text = item.get("text", "").strip()
            if not text:
                continue
            start = max(0.0, min(duration, float(item.get("start", 0))))
            end = max(start, min(duration, float(item.get("end", duration))))
            logprob = item.get("avg_logprob")
            segments.append(ASRSegment(
                start_time=offset_seconds + start, end_time=offset_seconds + end, text=text,
                language=language, confidence=min(1.0, math.exp(logprob)) if logprob is not None else 0.8,
                avg_logprob=logprob, no_speech_prob=item.get("no_speech_prob"),
                compression_ratio=item.get("compression_ratio"),
            ))
        return segments

    def transcribe_words(self, samples: np.ndarray, sample_rate: int, *, forced_language=None,
                         offset_seconds=0.0, initial_prompt=None) -> list[Word]:
        if samples.size == 0:
            return []
        result = self._decode(samples, sample_rate, language=forced_language, prompt=initial_prompt, words=True)
        words = []
        for segment in result.get("segments", []):
            for item in segment.get("words", []):
                text = item.get("word", "").strip()
                if text and item.get("start", -1) >= 0 and item.get("end", -1) >= item.get("start", 0):
                    words.append(Word(start=offset_seconds + float(item["start"]),
                                      end=offset_seconds + float(item["end"]), text=text))
        return words
