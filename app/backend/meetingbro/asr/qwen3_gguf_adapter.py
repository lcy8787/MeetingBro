"""Managed local llama.cpp audio transcription for the Cantonese HK checkpoint."""
from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from urllib.request import Request, urlopen

import numpy as np
import soundfile as sf

from .base import ASRAdapter, ASRSegment

ROOT = Path(__file__).resolve().parents[4]
MODEL_DIR = ROOT / "models" / "Qwen3-ASR-1.7B-Cantonese-HK-GGUF"
MODEL = "Qwen3-ASR-1.7B-Cantonese-HK.Q4_K_M.gguf"
PROJECTOR = "Qwen3-ASR-1.7B-Cantonese-HK.mmproj-Q8_0.gguf"


class Qwen3GGUFAdapter(ASRAdapter):
    def __init__(self):
        self.model_dir = Path(os.environ.get("MEETINGBRO_QWEN3_GGUF_MODEL_DIR", MODEL_DIR)).expanduser()
        binary = ROOT / ".runtime" / "llama-gguf" / "bin" / ("llama-server.exe" if os.name == "nt" else "llama-server")
        self.binary = Path(os.environ.get("MEETINGBRO_LLAMA_SERVER_PATH", binary)).expanduser()
        self._process = None
        self._lock = threading.RLock()
        self._closing = threading.Event()
        self._base = ""
        self._log = None

    def runtime_error(self):
        process = self._process
        if process is not None and process.poll() is not None and not self._closing.is_set():
            return "llama-server stopped. Restart the backend. See .runtime/gguf-server.log"
        return None

    def ensure_loaded(self):
        with self._lock:
            if self._closing.is_set():
                raise RuntimeError("GGUF backend is closing")
            if self._process is not None:
                if self._process.poll() is not None:
                    raise RuntimeError("llama-server stopped. Restart the backend and check the GGUF log.")
                return
            required = (self.binary, self.model_dir / MODEL, self.model_dir / PROJECTOR)
            if any(not path.is_file() for path in required):
                raise RuntimeError("Install Cantonese GGUF with scripts/install_qwen3_gguf.sh or scripts/install_qwen3_gguf.ps1")
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            self._base = f"http://127.0.0.1:{port}"
            log_path = ROOT / ".runtime" / "gguf-server.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log = log_path.open("ab", buffering=0)
            args = [str(self.binary), "-m", str(self.model_dir / MODEL), "--mmproj", str(self.model_dir / PROJECTOR),
                    "--host", "127.0.0.1", "--port", str(port), "-c", "4096", "-np", "1"]
            if sys.platform == "darwin" and os.uname().machine == "arm64":
                args += ["-ngl", "99"]
            elif (ROOT / ".runtime" / "llama-gguf" / "installation.json").is_file():
                metadata = json.loads((ROOT / ".runtime" / "llama-gguf" / "installation.json").read_text())
                if metadata.get("cuda"):
                    args += ["-ngl", "99"]
            try:
                self._process = subprocess.Popen([sys.executable, str(Path(__file__).with_name("gguf_process.py")), *args], stdin=subprocess.PIPE, stdout=self._log, stderr=self._log,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
                deadline = time.monotonic() + 180
                while not self._closing.is_set() and time.monotonic() < deadline:
                    if self._process.poll() is not None:
                        raise RuntimeError(f"llama-server failed. See {log_path}")
                    try:
                        with urlopen(self._base + "/health", timeout=1) as response:
                            if response.status == 200:
                                return
                    except OSError:
                        pass
                    self._closing.wait(.2)
                raise RuntimeError(f"GGUF loading cancelled or timed out. See {log_path}")
            except BaseException:
                self._stop()
                raise

    def _stop(self):
        process, self._process = self._process, None
        if process is not None and process.poll() is None:
            process.stdin.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if self._log is not None:
            self._log.close()
            self._log = None

    def close(self):
        self._closing.set()
        # Interrupt a request before waiting for its serialization lock.
        process = self._process
        if process is not None and process.poll() is None:
            process.stdin.close()
        with self._lock:
            self._stop()

    def transcribe(self, samples, sample_rate, *, forced_language=None, offset_seconds=0., initial_prompt=None, quality_preset="realtime"):
        if sample_rate != 16000 or samples.ndim != 1:
            raise ValueError("GGUF ASR expects mono 16 kHz audio")
        if not samples.size:
            return []
        with self._lock:
            self.ensure_loaded()
            segments = []
            for start in range(0, len(samples), 30 * sample_rate):
                audio = samples[start:start + 30 * sample_rate]
                buffer = io.BytesIO()
                sf.write(buffer, np.asarray(audio, dtype=np.float32), sample_rate, format="WAV", subtype="PCM_16")
                messages = []
                if initial_prompt:
                    messages.append({"role": "system", "content": initial_prompt})
                messages.append({"role": "user", "content": [{"type": "input_audio", "input_audio": {
                    "data": base64.b64encode(buffer.getvalue()).decode("ascii"), "format": "wav"}}]})
                request = Request(self._base + "/v1/chat/completions", data=json.dumps({
                    "messages": messages, "temperature": 0, "max_tokens": 512, "stream": False}).encode(),
                    headers={"Content-Type": "application/json"})
                try:
                    with urlopen(request, timeout=60) as response:
                        choice = json.load(response)["choices"][0]
                    if choice.get("finish_reason") == "length":
                        raise ValueError("transcript exceeded output limit")
                    content = choice["message"]["content"].strip()
                    if not content:
                        continue
                    header, marker, text = content.partition("<asr_text>")
                    if not marker:
                        raise ValueError("missing ASR text marker")
                    text = text.strip()
                    language = {"language Cantonese": "zh", "language Chinese": "zh", "language English": "en",
                                "language German": "de"}.get(header.strip(), "unknown")
                    if text:
                        begin = offset_seconds + start / sample_rate
                        segments.append(ASRSegment(begin, begin + len(audio) / sample_rate, text, language, .8))
                except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
                    raise RuntimeError(f"GGUF transcription failed: {exc}") from exc
            return segments
