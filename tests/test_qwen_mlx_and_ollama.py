from types import SimpleNamespace
import sys

import numpy as np

from meetingbro.asr.qwen3_mlx_adapter import Qwen3MLXAdapter
from meetingbro.llm.openai_compatible import OpenAICompatibleClient, is_local_ollama_url


def test_mlx_reuses_session_and_maps_audio(tmp_path, monkeypatch):
    for name in ('config.json', 'vocab.json', 'merges.txt', 'weights.safetensors'):
        (tmp_path / name).touch()
    calls = []
    class FakeSession:
        def __init__(self, model):
            calls.append(("load", model))
        def transcribe(self, audio, **kwargs):
            calls.append(("transcribe", kwargs))
            return SimpleNamespace(text="hello", language="English", truncated=False)
    monkeypatch.setitem(sys.modules, "mlx_qwen3_asr", SimpleNamespace(Session=FakeSession))
    adapter = Qwen3MLXAdapter(str(tmp_path))
    for _ in range(2):
        result = adapter.transcribe(np.zeros(16000, dtype=np.float32), 16000, forced_language="en", offset_seconds=2)
        assert (result[0].text, result[0].language, result[0].start_time, result[0].end_time) == ("hello", "en", 2, 3)
    assert sum(call[0] == "load" for call in calls) == 1
    assert calls[1][1]["language"] == "English"
    adapter.transcribe(np.zeros(16000, dtype=np.float32), 16000, forced_language="yue")
    assert calls[-1][1]["language"] is None


def test_local_ollama_needs_no_key(monkeypatch):
    monkeypatch.setenv("MEETINGBRO_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("MEETINGBRO_LLM_MODEL", "local-model")
    monkeypatch.delenv("MEETINGBRO_LLM_API_KEY", raising=False)
    monkeypatch.delenv("LONGCAT_API_KEY", raising=False)
    monkeypatch.setattr("meetingbro.llm.openai_compatible._DOTENV_LOADED", True)
    client = OpenAICompatibleClient.from_env()
    assert client is not None and client.model == "local-model"
    assert is_local_ollama_url("http://localhost:11434/v1")
    assert not is_local_ollama_url("http://example.com:11434/v1")
    assert not is_local_ollama_url("http://127.0.0.1:8000/v1")
