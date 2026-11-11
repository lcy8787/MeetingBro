from datetime import datetime, timezone
from unittest.mock import Mock
import sys

import pytest

from meetingbro.asr import factory
from meetingbro.hardware import HardwareProfile
from meetingbro.schemas import TranscriptSegment
from meetingbro.summarization.heuristic import HeuristicSummarizer


def test_auto_selects_apple_engine_and_small_without_loading_or_downloading(monkeypatch):
    monkeypatch.setattr(factory, '_mlx_available', lambda: False)
    for key in ("ENGINE", "DEVICE", "SIZE"):
        monkeypatch.setenv(f"MEETINGBRO_WHISPER_{key}", "auto")
    constructor = Mock()
    monkeypatch.setattr(factory, "WhisperCppAdapter", constructor)
    profile = HardwareProfile(cpu_count=6, ctranslate2_cuda_available=False,
                              apple_metal_available=True, whisper_cpp_available=True,
                              recommended_whisper_engine="whisper-cpp",
                              recommended_whisper_size="small")
    adapter, engine, device, _ = factory.build_formal_asr(profile)
    assert (engine, device) == ("whisper-cpp", "metal")
    assert constructor.call_args.kwargs["model_size"] == "small"
    adapter.ensure_loaded.assert_not_called()


def test_auto_prefers_local_qwen_when_installed_but_explicit_whisper_remains_available(monkeypatch, tmp_path):
    for name in ('config.json', 'vocab.json', 'merges.txt', 'weights.safetensors'):
        (tmp_path / name).touch()
    monkeypatch.setattr(factory.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(factory.platform, 'machine', lambda: 'arm64')
    monkeypatch.setattr(factory.importlib.util, 'find_spec', lambda name: object())
    monkeypatch.setenv('MEETINGBRO_QWEN3_MLX_MODEL', str(tmp_path))
    monkeypatch.setenv('MEETINGBRO_WHISPER_ENGINE', 'auto')
    profile = HardwareProfile(cpu_count=8, ctranslate2_cuda_available=False)
    adapter, engine, device, compute = factory.build_formal_asr(profile)
    assert (engine, device, compute) == ('qwen3-mlx', 'metal', 'mlx')
    assert adapter._session is None
    monkeypatch.setenv('MEETINGBRO_WHISPER_ENGINE', 'faster-whisper')
    monkeypatch.setattr(factory, 'FasterWhisperAdapter', Mock())
    _, engine, _, _ = factory.build_formal_asr(profile)
    assert engine == 'faster-whisper'


@pytest.mark.skipif(sys.platform != "darwin", reason="Uses the built-in macOS script converter")
def test_offline_chinese_extracts_are_traditional_and_preserve_english():
    segment = TranscriptSegment(id="1", meeting_id="1", start_time=0, end_time=5,
        text="会议预算与项目进度。 Release is Friday.", original_language="zh", confidence=0.9,
        created_at=datetime.now(timezone.utc))
    summarizer = HeuristicSummarizer()
    result = summarizer.summarize([segment], kind="cumulative_meeting_summary", language="zh")
    assert "## 會議狀態" in result
    assert "會議預算與項目進度" in result
    assert "Release is Friday." in result
    english = summarizer.summarize([segment], kind="rolling_summary", language="en")
    assert english.startswith("Recent discussion:")
    assert "会议预算" in english
