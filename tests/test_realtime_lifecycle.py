import asyncio
from unittest.mock import Mock

import numpy as np
import pytest

from meetingbro.asr.base import ASRSegment
from meetingbro.audio.capture import AudioChunk, AudioSource
from meetingbro.session.manager import SessionConfig, SessionManager
from meetingbro.storage.db import Storage
from meetingbro.summarization.heuristic import HeuristicSummarizer
from meetingbro.translation.passthrough import PassthroughTranslator


class FiniteSource(AudioSource):
    @property
    def sample_rate(self):
        return 16000

    async def stream(self):
        for i in range(4):
            samples = (np.sin(2 * np.pi * 440 * np.arange(8000) / 16000) * 0.03).astype(np.float32)
            yield AudioChunk(samples=samples, sample_rate=16000, start_time=i * 0.5)
            await asyncio.sleep(0)


class IncompleteASR:
    def transcribe(self, samples, sample_rate, **kwargs):
        return [ASRSegment(start_time=0, end_time=len(samples)/sample_rate,
                           text="The meeting is discussing", language="en", confidence=0.95)]


def config(storage, **kwargs):
    return SessionConfig(audio_source=FiniteSource(), asr=IncompleteASR(), storage=storage,
                         summarizer=HeuristicSummarizer(), translator=PassthroughTranslator(), **kwargs)


def test_short_endpoint_does_not_trigger_overload_and_metal_is_gpu():
    manager = SessionManager(config(Mock(), formal_asr_device="metal", compute_gpu_available=True))
    try:
        manager._record_asr_timing(audio_seconds=0.2, wall_seconds=0.6, phase="realtime")
        assert manager._state.asr_realtime_factor == pytest.approx(3)
        assert not manager._asr_safeguard_active()
        assert manager._update_resource_pressure() == "normal"
        manager._state.asr_inflight_start_wall_time = 1
        cpu, gpu, label = manager._compute_activity_status()
        assert gpu and not cpu and "metal" in label
    finally:
        manager._shutdown_executors()


def test_finite_source_commits_incomplete_speech_and_ends_without_false_watchdog(tmp_path):
    async def run():
        storage = Storage(tmp_path / "test.db")
        manager = SessionManager(config(storage, pre_vad_enabled=False, fast_preview_enabled=False,
                                         formal_incremental_commit=True, asr_accumulation_seconds=1,
                                         watchdog_tick_seconds=0.02, watchdog_no_chunk_warning_seconds=0.2))
        events = []
        async def consume():
            async for event in manager.events():
                events.append(event)
        collector = asyncio.create_task(consume())
        try:
            await manager.start()
            await asyncio.wait_for(manager._task, timeout=5)
            await asyncio.wait_for(collector, timeout=2)
            assert any(e.type == "transcript_segment" for e in events)
            assert any(e.type == "session_state" and e.payload["state"] == "ended" for e in events)
            assert manager._stopped.is_set()
            assert not any(e.type == "error" and e.payload.get("code") == "audio_source_silent" for e in events)
        finally:
            await manager.stop()
            storage.close()
    asyncio.run(run())


def test_shared_preview_reuses_id_and_revision_without_persisting(tmp_path):
    async def run():
        storage = Storage(tmp_path / 'preview.db')
        manager = SessionManager(config(storage, preview_asr_backend_name='shared'))
        try:
            samples = np.full(16000, 0.03, dtype=np.float32)
            loop = asyncio.get_running_loop()
            first = await manager._transcribe_fast_preview_window(loop, samples, 16000, buf_start=0, prompt=None, forced_language=None)
            second = await manager._transcribe_fast_preview_window(loop, samples, 16000, buf_start=0, prompt=None, forced_language=None)
            assert first is not None and second is not None
            assert first.id == second.id and (first.revision, second.revision) == (1, 2)
            assert first.status == second.status == 'partial'
            assert storage.list_segments(manager._state.meeting_id) == []
            await manager.pause()
            third = await manager._transcribe_fast_preview_window(loop, samples, 16000, buf_start=1, prompt=None, forced_language=None)
            assert third is not None and third.id != first.id and third.revision == 1
        finally:
            manager._shutdown_executors()
            storage.close()
    asyncio.run(run())


def test_shared_preview_emits_before_formal_and_is_not_promoted(tmp_path):
    class TimedSource(AudioSource):
        @property
        def sample_rate(self):
            return 16000
        async def stream(self):
            samples = (np.sin(2 * np.pi * 440 * np.arange(8000) / 16000) * 0.03).astype(np.float32)
            for i in range(8):
                yield AudioChunk(samples=samples, sample_rate=16000, start_time=i * 0.5)
                await asyncio.sleep(0.16)

    async def run():
        storage = Storage(tmp_path / 'live.db')
        cfg = config(storage, pre_vad_enabled=False, fast_preview_enabled=True,
                     fast_preview_interval_seconds=0.2, fast_preview_window_seconds=3,
                     asr_accumulation_seconds=3.5, preview_asr_backend_name='shared',
                     formal_incremental_commit=True, silence_rms_threshold=0.001)
        cfg.audio_source = TimedSource()
        manager = SessionManager(cfg)
        events = []
        async def collect():
            async for event in manager.events():
                events.append(event)
        collector = asyncio.create_task(collect())
        try:
            await manager.start()
            await asyncio.wait_for(manager._task, 8)
            await asyncio.wait_for(collector, 2)
            previews = [e for e in events if e.type == 'transcript_preview' and e.payload['segment'] and e.payload['segment']['status'] == 'partial']
            formal = [e for e in events if e.type == 'transcript_segment']
            assert previews and formal
            assert events.index(previews[0]) < events.index(formal[0])
            assert all(s.status == 'final' for s in storage.list_segments(manager._state.meeting_id))
            assert not manager._state.qwen_committed_drafts
        finally:
            await manager.stop()
            storage.close()
    asyncio.run(run())


def test_preview_completed_after_pause_and_resume_is_discarded(tmp_path):
    import threading

    class SlowASR:
        def __init__(self):
            self.started = threading.Event()
            self.release = threading.Event()
            self.calls = 0
        def transcribe(self, samples, sample_rate, **kwargs):
            self.calls += 1
            if self.calls == 1:
                self.started.set()
                self.release.wait(3)
                text = 'old preview before pause'
            else:
                text = 'new content after pause'
            return [ASRSegment(start_time=0, end_time=len(samples) / sample_rate,
                               text=text, language='en', confidence=0.95)]

    class TimedSource(AudioSource):
        @property
        def sample_rate(self):
            return 16000
        async def stream(self):
            samples = (np.sin(2 * np.pi * 440 * np.arange(8000) / 16000) * 0.03).astype(np.float32)
            for i in range(12):
                yield AudioChunk(samples=samples, sample_rate=16000, start_time=i * 0.5)
                await asyncio.sleep(0.12)

    async def run():
        storage = Storage(tmp_path / 'late.db')
        cfg = config(storage, pre_vad_enabled=False, fast_preview_enabled=True,
                     fast_preview_interval_seconds=0.2, fast_preview_window_seconds=3,
                     asr_accumulation_seconds=10, preview_asr_backend_name='shared')
        cfg.audio_source, cfg.asr = TimedSource(), SlowASR()
        manager = SessionManager(cfg)
        events = []
        async def collect():
            async for event in manager.events():
                events.append(event)
        collector = asyncio.create_task(collect())
        try:
            await manager.start()
            assert await asyncio.to_thread(cfg.asr.started.wait, 3)
            await manager.pause()
            await manager.resume()
            cfg.asr.release.set()
            await asyncio.wait_for(manager._task, 8)
            await asyncio.wait_for(collector, 2)
            previews = [e.payload['segment']['text'] for e in events
                        if e.type == 'transcript_preview' and e.payload['segment']]
            assert 'old preview before pause' not in previews
        finally:
            cfg.asr.release.set()
            await manager.stop()
            storage.close()
    asyncio.run(run())


@pytest.mark.parametrize('vad_enabled', [False, True])
@pytest.mark.parametrize('repeat_text', [False, True])
def test_pause_flushes_audio_before_resuming_without_duplicate_or_paused_audio(tmp_path, monkeypatch, vad_enabled, repeat_text):
    from meetingbro.audio.vad import SileroSpeechDetector, SpeechSpan
    monkeypatch.setattr(SileroSpeechDetector, 'detect', lambda self, samples, rate: [SpeechSpan(0, len(samples))])
    class ControlledSource(AudioSource):
        def __init__(self):
            self.items = asyncio.Queue()
        @property
        def sample_rate(self):
            return 16000
        async def stream(self):
            while True:
                chunk = await self.items.get()
                if chunk is None:
                    self.items.task_done()
                    return
                yield chunk
                self.items.task_done()
        async def aclose(self):
            await self.items.put(None)

    class CountingASR:
        def __init__(self):
            self.durations = []
        def transcribe(self, samples, sample_rate, **kwargs):
            duration = len(samples) / sample_rate
            self.durations.append(duration)
            return [ASRSegment(start_time=0, end_time=duration,
                text='Repeated sentence.' if repeat_text else f'Captured passage number {len(self.durations)}.',
                language='en', confidence=0.95)]

    async def run():
        storage = Storage(tmp_path / 'pause.db')
        source, asr = ControlledSource(), CountingASR()
        cfg = config(storage, pre_vad_enabled=vad_enabled, fast_preview_enabled=False,
                     asr_accumulation_seconds=10, formal_incremental_commit=True)
        cfg.audio_source, cfg.asr = source, asr
        manager = SessionManager(cfg)
        samples = (np.sin(2 * np.pi * 440 * np.arange(8000) / 16000) * 0.03).astype(np.float32)
        try:
            await manager.start()
            await source.items.put(AudioChunk(samples=samples, sample_rate=16000, start_time=0))
            await asyncio.wait_for(source.items.join(), 2)
            await asyncio.wait_for(manager.pause(), 3)
            saved = storage.list_segments(manager._state.meeting_id)
            assert len(saved) == 1 and saved[0].start_time == 0 and saved[0].end_time == 0.5
            await source.items.put(AudioChunk(samples=samples, sample_rate=16000, start_time=0.5))
            await asyncio.wait_for(source.items.join(), 2)
            await manager.resume()
            await source.items.put(AudioChunk(samples=samples, sample_rate=16000, start_time=1))
            await asyncio.wait_for(source.items.join(), 2)
            await source.items.put(None)
            await asyncio.wait_for(manager._task, 4)
            saved = storage.list_segments(manager._state.meeting_id)
            assert len(saved) == 2
            assert [(s.start_time, s.end_time) for s in saved] == [(0, 0.5), (1, 1.5)]
            assert asr.durations == [0.5, 0.5]
        finally:
            await manager.stop()
            storage.close()
    asyncio.run(run())


def test_graceful_stop_drains_audio_already_queued_and_emits_final_events(tmp_path):
    import time
    class BurstSource(AudioSource):
        def __init__(self):
            self.enqueued = asyncio.Event()
            self.closed = asyncio.Event()
        @property
        def sample_rate(self):
            return 16000
        async def stream(self):
            samples = (np.sin(2 * np.pi * 440 * np.arange(8000) / 16000) * 0.03).astype(np.float32)
            for i in range(4):
                yield AudioChunk(samples=samples, sample_rate=16000, start_time=i * 0.5)
            self.enqueued.set()
            await self.closed.wait()
        async def aclose(self):
            self.closed.set()

    class SlowASR:
        def __init__(self):
            self.calls = 0
        def transcribe(self, samples, sample_rate, **kwargs):
            self.calls += 1
            time.sleep(0.05)
            return [ASRSegment(start_time=0, end_time=len(samples) / sample_rate,
                               text=f'Unique spoken sentence {self.calls}.', language='en', confidence=0.95)]

    async def run():
        storage = Storage(tmp_path / 'stop.db')
        source, asr = BurstSource(), SlowASR()
        cfg = config(storage, pre_vad_enabled=False, fast_preview_enabled=False,
                     asr_accumulation_seconds=0.5, formal_incremental_commit=True,
                     audio_input_queue_max_seconds=5)
        cfg.audio_source, cfg.asr = source, asr
        manager = SessionManager(cfg)
        events = []
        async def collect():
            async for event in manager.events():
                events.append(event)
        collector = asyncio.create_task(collect())
        try:
            await manager.start()
            await asyncio.wait_for(source.enqueued.wait(), 2)
            await asyncio.wait_for(manager.stop(graceful=True), 5)
            await asyncio.wait_for(collector, 2)
            segments = storage.list_segments(manager._state.meeting_id)
            assert segments and segments[-1].end_time == 2
            assert any(e.type == 'transcript_segment' and e.payload['end_time'] == 2 for e in events)
            assert manager._state.audio_input_queue_drop_total == 0
        finally:
            if not manager._stopped.is_set():
                await manager.stop()
            storage.close()
    asyncio.run(run())


def test_shared_preview_identity_becomes_a_newer_final_revision(tmp_path):
    async def run():
        storage = Storage(tmp_path / 'revision.db')
        manager = SessionManager(config(storage, preview_asr_backend_name='shared', reuse_formal_preview_identity=True))
        try:
            samples = np.full(16000, 0.03, dtype=np.float32)
            loop = asyncio.get_running_loop()
            first = await manager._transcribe_fast_preview_window(loop, samples, 16000, buf_start=0, prompt=None, forced_language=None)
            latest = await manager._transcribe_fast_preview_window(loop, samples, 16000, buf_start=0, prompt=None, forced_language=None)
            assert first and latest
            manager._state.fast_preview_segment = latest
            formal = latest.model_copy(update={'id': 'formal-id', 'text': 'Final corrected text', 'status': 'final', 'revision': 1})
            await manager._persist_and_emit_segment(formal)
            saved = storage.list_segments(manager._state.meeting_id)
            assert len(saved) == 1
            assert saved[0].id == latest.id and saved[0].revision == 3
            assert saved[0].sequence == latest.sequence == 1 and saved[0].status == 'final'
            assert saved[0].text == 'Final corrected text'
            with pytest.raises(ValueError):
                storage.insert_segment(latest)
            storage.insert_segment(saved[0].model_copy(update={'text': 'stale', 'revision': 2}))
            assert storage.list_segments(manager._state.meeting_id)[0].text == 'Final corrected text'
        finally:
            manager._shutdown_executors()
            storage.close()
    asyncio.run(run())


def test_summary_switches_disable_automatic_jobs_without_disabling_transcription(tmp_path):
    async def run():
        storage = Storage(tmp_path / 'switches.db')
        called = []
        async def enqueue(*args):
            called.append(args)
            return {}
        manager = SessionManager(config(storage, pre_vad_enabled=False, fast_preview_enabled=False,
            formal_incremental_commit=True, asr_accumulation_seconds=1,
            live_summaries_enabled=False, final_summary_enabled=False, enqueue_final_summary=enqueue))
        try:
            await manager.start()
            await asyncio.wait_for(manager._task, 4)
            assert storage.list_segments(manager._state.meeting_id)
            assert not storage.list_snapshots(manager._state.meeting_id)
            assert not called
        finally:
            await manager.stop()
            storage.close()
    asyncio.run(run())
