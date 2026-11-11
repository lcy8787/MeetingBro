import asyncio
import threading
from datetime import datetime, timezone

from meetingbro.jobs.final_summary import FinalSummaryWorker
from meetingbro.schemas import TranscriptSegment
from meetingbro.storage.db import Storage


def add_meeting(storage):
    storage.create_meeting('meeting')
    storage.insert_segment(TranscriptSegment(id='segment', meeting_id='meeting', start_time=0, end_time=2,
        text='A transcript with an agreed action.', original_language='en', confidence=1, created_at=datetime.now(timezone.utc)))


async def wait_for_status(storage, status):
    async def poll():
        while True:
            job = storage.get_final_summary_job('meeting')
            if job and job['status'] == status:
                return job
            await asyncio.sleep(0.01)
    return await asyncio.wait_for(poll(), 3)


class GoodSummary:
    def finalize_meeting(self, segments, **kwargs):
        assert [s.id for s in segments] == ['segment']
        return {'final_summary': 'Agreed action.', 'chapters': [], 'action_items': []}


def test_final_job_failure_retry_and_atomic_snapshots(tmp_path):
    class Failing:
        def finalize_meeting(self, segments, **kwargs):
            raise RuntimeError('offline')
    async def run():
        storage = Storage(tmp_path / 'db.sqlite')
        add_meeting(storage)
        factory = [Failing]
        worker = FinalSummaryWorker(storage, lambda: factory[0]())
        worker.start()
        try:
            job = await worker.enqueue('meeting', 'en')
            assert job['status'] == 'queued'
            failed = await wait_for_status(storage, 'failed')
            assert failed['attempts'] == 1 and 'offline' in failed['error']
            assert storage.list_snapshots('meeting') == []
            factory[0] = GoodSummary
            retried = await worker.enqueue('meeting', 'en')
            assert retried['id'] == job['id'] and retried['status'] == 'queued'
            completed = await wait_for_status(storage, 'succeeded')
            assert completed['attempts'] == 2
            snapshots = storage.list_snapshots('meeting')
            assert {s.summary_type for s in snapshots} == {'final_summary', 'chapter_list', 'action_item_list'}
            assert all(s.source_segment_ids == ['segment'] for s in snapshots)
            assert storage.list_report_revisions('meeting')[-1].document.sections[0].blocks[0].text == 'Agreed action.'
        finally:
            await worker.close()
            storage.close()
    asyncio.run(run())


def test_inflight_job_recovers_after_worker_restart(tmp_path):
    class SlowSummary:
        started = threading.Event()
        release = threading.Event()
        def finalize_meeting(self, segments, **kwargs):
            self.started.set()
            self.release.wait(3)
            return {'final_summary': 'Old interrupted result', 'chapters': [], 'action_items': []}
    async def run():
        storage = Storage(tmp_path / 'db.sqlite')
        add_meeting(storage)
        slow = FinalSummaryWorker(storage, SlowSummary)
        slow.start()
        await slow.enqueue('meeting', 'en')
        assert await asyncio.to_thread(SlowSummary.started.wait, 2)
        await slow.close()
        assert storage.get_final_summary_job('meeting')['status'] == 'running'
        fresh = FinalSummaryWorker(storage, GoodSummary)
        fresh.start()
        try:
            completed = await wait_for_status(storage, 'succeeded')
            assert completed['attempts'] == 2
            SlowSummary.release.set()
            await asyncio.sleep(0.05)
            assert storage.list_snapshots('meeting', 'final_summary')[-1].content == 'Agreed action.'
        finally:
            SlowSummary.release.set()
            await fresh.close()
            storage.close()
    asyncio.run(run())


def test_session_finishes_while_slow_final_job_continues(tmp_path):
    import numpy as np
    from meetingbro.asr.base import ASRSegment
    from meetingbro.audio.capture import AudioChunk, AudioSource
    from meetingbro.session.manager import SessionConfig, SessionManager
    from meetingbro.summarization.heuristic import HeuristicSummarizer
    from meetingbro.translation.passthrough import PassthroughTranslator

    class Source(AudioSource):
        @property
        def sample_rate(self): return 16000
        async def stream(self):
            samples = (np.sin(2 * np.pi * 440 * np.arange(16000) / 16000) * 0.03).astype(np.float32)
            yield AudioChunk(samples=samples, sample_rate=16000, start_time=0)

    class ASR:
        def transcribe(self, samples, sample_rate, **kwargs):
            return [ASRSegment(start_time=0, end_time=len(samples)/sample_rate,
                               text='The agreed action is clear.', language='en', confidence=1)]

    class SlowSummary:
        def __init__(self, started, release):
            self.started, self.release = started, release
        def finalize_meeting(self, segments, **kwargs):
            self.started.set()
            self.release.wait(3)
            return {'final_summary': 'The agreed action is clear.', 'chapters': [], 'action_items': []}

    async def run():
        storage = Storage(tmp_path / 'session.sqlite')
        started, release = threading.Event(), threading.Event()
        worker = FinalSummaryWorker(storage, lambda: SlowSummary(started, release))
        worker.start()
        cfg = SessionConfig(audio_source=Source(), asr=ASR(), storage=storage,
            summarizer=HeuristicSummarizer(), translator=PassthroughTranslator(),
            fast_preview_enabled=False, pre_vad_enabled=False, formal_incremental_commit=True,
            enqueue_final_summary=worker.enqueue)
        manager = SessionManager(cfg)
        try:
            await manager.start()
            await asyncio.wait_for(manager._task, 3)
            assert await asyncio.to_thread(started.wait, 2)
            job = storage.get_final_summary_job(manager._state.meeting_id)
            assert job['status'] == 'running'
            assert not storage.list_snapshots(manager._state.meeting_id, 'final_summary')
            release.set()
            async def poll():
                while storage.get_final_summary_job(manager._state.meeting_id)['status'] != 'succeeded':
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(poll(), 3)
            assert storage.list_snapshots(manager._state.meeting_id, 'final_summary')[0].content == 'The agreed action is clear.'
        finally:
            release.set()
            await manager.stop()
            await worker.close()
            storage.close()
    asyncio.run(run())


def test_snapshot_failure_rolls_back_job_completion(tmp_path):
    import pytest
    import sqlite3
    from meetingbro.schemas import SummarySnapshot

    storage = Storage(tmp_path / 'atomic.sqlite')
    add_meeting(storage)
    storage.queue_final_summary('job', 'meeting', 'en', None)
    assert storage.claim_final_summary_job()['id'] == 'job'
    snap = SummarySnapshot(id='duplicate', meeting_id='meeting', summary_type='final_summary',
                           time_start=0, time_end=2, language='en', content='Summary',
                           source_segment_ids=['segment'], is_latest=True, created_at=datetime.now(timezone.utc))
    with pytest.raises(sqlite3.IntegrityError):
        storage.complete_final_summary_job('job', [snap, snap])
    assert storage.get_final_summary_job('meeting')['status'] == 'running'
    assert storage.list_snapshots('meeting') == []
    storage.close()


def test_missing_provider_is_reported_as_retryable_failure(tmp_path):
    class Unconfigured:
        def _ensure_client(self): return None
        def finalize_meeting(self, *args, **kwargs): raise AssertionError('should not silently fall back')
    async def run():
        storage = Storage(tmp_path / 'missing.sqlite')
        add_meeting(storage)
        worker = FinalSummaryWorker(storage, Unconfigured)
        worker.start()
        try:
            await worker.enqueue('meeting', 'en')
            job = await wait_for_status(storage, 'failed')
            assert 'No summary model is configured' in job['error']
            assert not storage.list_snapshots('meeting')
        finally:
            await worker.close()
            storage.close()
    asyncio.run(run())


def test_job_api_redacts_vocabulary_and_retries_only_failed(tmp_path):
    import pytest
    from fastapi import HTTPException
    from meetingbro.main import app, get_final_summary_job, retry_final_summary_job

    async def run():
        storage = Storage(tmp_path / 'api.sqlite')
        add_meeting(storage)
        worker = FinalSummaryWorker(storage, GoodSummary)
        app.state.storage, app.state.final_summary_worker = storage, worker
        try:
            storage.queue_final_summary('job', 'meeting', 'en', 'private vocabulary')
            response = await get_final_summary_job('meeting')
            assert response['status'] == 'queued' and 'vocabulary' not in response
            with pytest.raises(HTTPException) as conflict:
                await retry_final_summary_job('meeting')
            assert conflict.value.status_code == 409
            storage.claim_final_summary_job()
            storage.fail_final_summary_job('job', 'offline')
            response = await retry_final_summary_job('meeting')
            assert response['status'] == 'queued' and 'vocabulary' not in response
        finally:
            storage.close()
    asyncio.run(run())


def test_audio_flush_timeout_persists_incomplete_status(tmp_path):
    import time
    import numpy as np
    from meetingbro.asr.base import ASRSegment
    from meetingbro.audio.capture import AudioChunk, AudioSource
    from meetingbro.session.manager import SessionConfig, SessionManager
    from meetingbro.summarization.heuristic import HeuristicSummarizer
    from meetingbro.translation.passthrough import PassthroughTranslator

    class Source(AudioSource):
        @property
        def sample_rate(self): return 16000
        async def stream(self):
            samples = (np.sin(2*np.pi*440*np.arange(16000)/16000)*0.03).astype(np.float32)
            yield AudioChunk(samples=samples, sample_rate=16000, start_time=0)
            await asyncio.Event().wait()
        async def aclose(self): pass

    class SlowASR:
        started = threading.Event()
        def transcribe(self, samples, sample_rate, **kwargs):
            self.started.set()
            time.sleep(0.2)
            return [ASRSegment(start_time=0, end_time=1, text='speech', language='en', confidence=1)]

    async def run():
        storage = Storage(tmp_path / 'timeout.sqlite')
        async def enqueue(*args): raise AssertionError('incomplete transcript must not be queued')
        cfg = SessionConfig(audio_source=Source(), asr=SlowASR(), storage=storage,
                            summarizer=HeuristicSummarizer(), translator=PassthroughTranslator(),
                            pre_vad_enabled=False, fast_preview_enabled=False, asr_accumulation_seconds=0.5,
                            enqueue_final_summary=enqueue)
        manager = SessionManager(cfg)
        manager._STOP_TASK_TIMEOUT_SECONDS = 0.02
        try:
            await manager.start()
            assert await asyncio.to_thread(SlowASR.started.wait, 2)
            await asyncio.wait_for(manager.stop(graceful=True), 2)
            job = storage.get_final_summary_job(manager._state.meeting_id)
            assert job['status'] == 'failed' and 'incomplete' in job['error']
            assert job['attempts'] == 0
        finally:
            storage.close()
    asyncio.run(run())


def test_final_job_remains_queued_while_memory_is_low(tmp_path):
    from meetingbro.resources.coordinator import InferenceCoordinator
    async def run():
        storage = Storage(tmp_path / 'pressure.sqlite')
        add_meeting(storage)
        coordinator = InferenceCoordinator()
        coordinator.memory_free_percent = 1
        worker = FinalSummaryWorker(storage, GoodSummary, coordinator)
        worker.start()
        try:
            await worker.enqueue('meeting', 'en')
            await asyncio.sleep(0.3)
            assert storage.get_final_summary_job('meeting')['status'] == 'queued'
            coordinator.memory_free_percent = 50
            await wait_for_status(storage, 'succeeded')
        finally:
            await worker.close()
            await coordinator.close()
            storage.close()
    asyncio.run(run())
