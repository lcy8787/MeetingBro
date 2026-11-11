"""Native protocol, timestamps, offline safety, and device reporting."""
import io
import json
from unittest.mock import Mock

import numpy as np
import pytest

from meetingbro.asr.whisper_cpp_adapter import WhisperCppAdapter
from meetingbro.asr.factory import build_formal_asr
from meetingbro.hardware import HardwareProfile


def test_verbose_protocol_preserves_language_timestamps_and_privacy():
    adapter = WhisperCppAdapter()
    adapter.ensure_loaded = Mock()
    adapter._base = "http://127.0.0.1:1234"
    response = {"language": "chinese", "segments": [{"start": 0.2, "end": 3.5,
                "text": "會議開始", "avg_logprob": -0.2, "no_speech_prob": 0.01}]}
    adapter._http = Mock()
    adapter._http.open.return_value = io.BytesIO(json.dumps(response).encode())
    segments = adapter.transcribe(np.zeros(16000, dtype=np.float32), 16000, offset_seconds=10)
    assert segments[0].language == "zh"
    assert segments[0].start_time == 10.2
    assert segments[0].end_time == 11  # native endpoint padding cannot extend beyond captured audio
    assert segments[0].confidence > 0.8
    request = adapter._http.open.call_args.args[0]
    assert request.full_url == "http://127.0.0.1:1234/inference"
    assert b'name="no_language_probabilities"\r\n\r\ntrue' in request.data
    assert b'name="token_timestamps"\r\n\r\nfalse' in request.data


def test_missing_model_fails_without_attempting_download(monkeypatch, tmp_path):
    binary = tmp_path / "whisper-server"
    binary.touch()
    monkeypatch.setenv("MEETINGBRO_WHISPER_CPP_BINARY", str(binary))
    monkeypatch.setenv("MEETINGBRO_WHISPER_CPP_MODEL", str(tmp_path / "missing.bin"))
    with pytest.raises(RuntimeError, match="downloads require approval"):
        WhisperCppAdapter().ensure_loaded()


def test_factory_never_passes_metal_to_ctranslate2(monkeypatch):
    hardware = HardwareProfile(cpu_count=6, ctranslate2_cuda_available=False,
                               apple_metal_available=True, whisper_cpp_available=True,
                               recommended_whisper_engine="whisper-cpp")
    monkeypatch.setenv("MEETINGBRO_WHISPER_ENGINE", "faster-whisper")
    monkeypatch.setenv("MEETINGBRO_WHISPER_DEVICE", "auto")
    adapter, engine, device, compute = build_formal_asr(hardware)
    assert (engine, device, compute) == ("faster-whisper", "cpu", "int8")
    monkeypatch.setenv("MEETINGBRO_WHISPER_DEVICE", "metal")
    with pytest.raises(ValueError, match="Apple Metal"):
        build_formal_asr(hardware)


def test_factory_selects_native_gpu_only_when_supported(monkeypatch):
    monkeypatch.setenv("MEETINGBRO_WHISPER_ENGINE", "auto")
    monkeypatch.setenv("MEETINGBRO_WHISPER_DEVICE", "auto")
    hardware = HardwareProfile(cpu_count=6, ctranslate2_cuda_available=False,
                               apple_metal_available=True, whisper_cpp_available=True,
                               recommended_whisper_engine="whisper-cpp")
    adapter, engine, device, compute = build_formal_asr(hardware)
    assert isinstance(adapter, WhisperCppAdapter)
    assert (engine, device) == ("whisper-cpp", "metal")


def test_native_gpu_reporting_tracks_initialization_failure():
    adapter = WhisperCppAdapter()
    adapter._read_logs(io.StringIO("whisper_backend_init_gpu: using MTL0 backend\n"))
    assert adapter._metal_active
    adapter._read_logs(io.StringIO("whisper_backend_init_gpu: failed to initialize MTL0 backend\n"))
    assert not adapter._metal_active
