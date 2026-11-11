from meetingbro.main import _build_preview_asr, _configured_preview_runtime


def test_preview_always_shares_qwen_even_with_legacy_environment(monkeypatch):
    for backend in ("whisper-streaming", "whisper", "qwen3", "auto"):
        monkeypatch.setenv("MEETINGBRO_PREVIEW_ASR_BACKEND", backend)
        assert _configured_preview_runtime(None, formal_device="metal") == ("shared", "metal")
        assert _build_preview_asr() is None
