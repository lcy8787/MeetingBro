from datetime import datetime, timezone
from unittest.mock import Mock
import sys

import pytest

from meetingbro.asr import factory
from meetingbro.hardware import HardwareProfile
from meetingbro.schemas import TranscriptSegment
from meetingbro.summarization.heuristic import HeuristicSummarizer


@pytest.mark.parametrize("size", ["0.6B", "1.7B"])
def test_apple_uses_selected_qwen_without_loading_or_downloading(monkeypatch, size):
    monkeypatch.setattr(factory.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(factory.platform, "machine", lambda: "arm64")
    monkeypatch.setenv("MEETINGBRO_QWEN3_MODEL_SIZE", size)
    monkeypatch.delenv("MEETINGBRO_QWEN3_MLX_MODEL", raising=False)
    monkeypatch.setenv("MEETINGBRO_WHISPER_ENGINE", "faster-whisper")
    profile = HardwareProfile(cpu_count=6, apple_metal_available=True)
    adapter, engine, device, compute = factory.build_formal_asr(profile)
    assert (engine, device, compute) == ("qwen3-mlx", "metal", "mlx")
    assert adapter.model_path.name == f"Qwen3-ASR-{size}-4bit"
    assert adapter._session is None


def test_other_platform_uses_qwen_and_invalid_size_is_rejected(monkeypatch):
    monkeypatch.setattr(factory.platform, "system", lambda: "Linux")
    monkeypatch.setenv("MEETINGBRO_QWEN3_MODEL_SIZE", "1.7B")
    adapter, engine, device, _ = factory.build_formal_asr(HardwareProfile(cpu_count=8, cuda_available=True))
    assert (engine, device) == ("qwen3", "cuda")
    assert adapter.model_path.name == "Qwen3-ASR-1.7B" and adapter._model is None
    monkeypatch.setenv("MEETINGBRO_QWEN3_MODEL_SIZE", "1.6B")
    with pytest.raises(ValueError): factory.build_formal_asr(HardwareProfile(cpu_count=8))


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
