import io
import json
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from meetingbro.asr.qwen3_gguf_adapter import Qwen3GGUFAdapter


def test_audio_request_and_segment_mapping(monkeypatch):
    calls = []
    def request(req, timeout):
        import base64
        payload = json.loads(req.data)
        calls.append(payload)
        audio = payload["messages"][-1]["content"][0]["input_audio"]
        samples, rate = sf.read(io.BytesIO(base64.b64decode(audio["data"])))
        assert rate == 16000
        assert samples.ndim == 1
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": "language Cantonese<asr_text>喺 GitHub 開會"}, "finish_reason": "stop"}]}).encode())
    monkeypatch.setattr("meetingbro.asr.qwen3_gguf_adapter.urlopen", request)
    adapter = Qwen3GGUFAdapter()
    adapter._base = "http://127.0.0.1:8180"
    monkeypatch.setattr(adapter, "ensure_loaded", lambda: None)
    result = adapter.transcribe(np.zeros(16000, dtype=np.float32), 16000, offset_seconds=2, initial_prompt="GitHub")
    assert (result[0].text, result[0].language, result[0].start_time, result[0].end_time) == ("喺 GitHub 開會", "zh", 2, 3)
    assert calls[0]["messages"][0] == {"role": "system", "content": "GitHub"}
    assert adapter.transcribe(np.array([]), 16000) == []
    result = adapter.transcribe(np.zeros(31 * 16000), 16000, offset_seconds=4)
    assert len(result) == 2
    assert result[-1].start_time == 34


@pytest.mark.parametrize("choice", [
    {"message": {"content": "bad response"}},
    {"message": {"content": "language Cantonese<asr_text>text"}, "finish_reason": "length"},
])
def test_invalid_result_is_error(monkeypatch, choice):
    adapter = Qwen3GGUFAdapter()
    adapter._base = "http://127.0.0.1:8180"
    monkeypatch.setattr(adapter, "ensure_loaded", lambda: None)
    monkeypatch.setattr("meetingbro.asr.qwen3_gguf_adapter.urlopen", lambda *a, **k: io.BytesIO(json.dumps({"choices": [choice]}).encode()))
    with pytest.raises(RuntimeError, match="GGUF transcription failed"):
        adapter.transcribe(np.zeros(16000), 16000)


def test_factory_selects_gguf(monkeypatch):
    from meetingbro.asr.factory import build_formal_asr
    monkeypatch.setenv("MEETINGBRO_ASR_MODEL", "cantonese-hk-gguf")
    adapter, engine, device, quant = build_formal_asr(SimpleNamespace(cuda_available=False))
    assert isinstance(adapter, Qwen3GGUFAdapter)
    assert engine == "qwen3-gguf"
    assert quant == "Q4_K_M"


def test_missing_install_and_closed_adapter():
    adapter = Qwen3GGUFAdapter()
    adapter.binary = adapter.binary / "missing"
    with pytest.raises(RuntimeError, match="Install Cantonese"):
        adapter.ensure_loaded()
    adapter.close()
    with pytest.raises(RuntimeError, match="closing"):
        adapter.ensure_loaded()


def test_process_guard_cleans_child_when_parent_pipe_closes(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys
    import time

    pid_path = tmp_path / "child.pid"
    code = "import os, pathlib, time; pathlib.Path(" + repr(str(pid_path)) + ").write_text(str(os.getpid())); time.sleep(60)"
    guard = Path(__file__).resolve().parents[1] / "app/backend/meetingbro/asr/gguf_process.py"
    process = subprocess.Popen([sys.executable, str(guard), sys.executable, "-c", code], stdin=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 5
        while not pid_path.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        assert pid_path.exists()
        child_pid = int(pid_path.read_text())
        process.stdin.close()
        process.wait(timeout=5)
        if os.name != "nt":
            with pytest.raises(ProcessLookupError):
                os.kill(child_pid, 0)
    finally:
        if process.poll() is None:
            process.stdin.close()
            process.wait(timeout=5)
