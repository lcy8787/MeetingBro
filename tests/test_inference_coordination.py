import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import Mock

from meetingbro.resources.coordinator import InferenceCoordinator
from meetingbro.session.manager import SessionConfig, SessionManager
from meetingbro.summarization.heuristic import HeuristicSummarizer
from meetingbro.translation.passthrough import PassthroughTranslator


def test_backlog_asr_and_memory_pressure_delay_new_inference():
    async def run():
        coordinator = InferenceCoordinator(minimum_free_percent=10, backlog_seconds=0.5)
        session = SimpleNamespace(_task=None, _state=SimpleNamespace(audio_input_backlog_seconds=2,
                                  asr_inflight_start_wall_time=None, fast_preview_inflight=False))
        # SimpleNamespace cannot be weak-referenced; use a normal holder.
        class Holder: pass
        holder = Holder(); holder._task = session._task; holder._state = session._state
        coordinator.register(holder)
        started = threading.Event()
        task = asyncio.create_task(coordinator.run(lambda: started.set()))
        await asyncio.sleep(0.3)
        assert not started.is_set() and coordinator.defer_reason() == 'audio backlog'
        holder._state.audio_input_backlog_seconds = 0
        holder._state.asr_inflight_start_wall_time = 1
        await asyncio.sleep(0.3)
        assert not started.is_set() and coordinator.defer_reason() == 'ASR inference'
        holder._state.asr_inflight_start_wall_time = None
        coordinator.memory_free_percent = 5
        await asyncio.sleep(0.3)
        assert not started.is_set() and coordinator.defer_reason() == 'memory pressure'
        coordinator.memory_free_percent = 20
        await asyncio.wait_for(task, 2)
        assert started.is_set()
        await coordinator.close()
    asyncio.run(run())


def test_cancelled_inflight_call_keeps_global_slot_until_thread_finishes():
    async def run():
        coordinator = InferenceCoordinator()
        first_started, release, second_started = threading.Event(), threading.Event(), threading.Event()
        def first():
            first_started.set(); release.wait(2)
        first_task = asyncio.create_task(coordinator.run(first))
        assert await asyncio.to_thread(first_started.wait, 1)
        first_task.cancel()
        await asyncio.gather(first_task, return_exceptions=True)
        second_task = asyncio.create_task(coordinator.run(lambda: second_started.set()))
        await asyncio.sleep(0.1)
        assert not second_started.is_set()
        release.set()
        await asyncio.wait_for(second_task, 2)
        assert second_started.is_set()
        await coordinator.close()
    asyncio.run(run())


def test_repeated_summary_requests_coalesce_by_kind():
    async def run():
        config = SessionConfig(audio_source=Mock(), asr=Mock(), storage=Mock(),
                               summarizer=HeuristicSummarizer(), translator=PassthroughTranslator())
        manager = SessionManager(config)
        release = asyncio.Event()
        async def rolling(): await release.wait()
        async def memory(): await release.wait()
        try:
            for _ in range(100): manager._schedule_summary(rolling())
            for _ in range(100): manager._schedule_summary(memory())
            assert len(manager._summary_tasks) == 2
            release.set()
            await asyncio.gather(*manager._summary_tasks)
            await asyncio.sleep(0)
            assert not manager._summary_tasks_by_kind
        finally:
            manager._shutdown_executors()
    asyncio.run(run())


def test_resource_mode_normalization(monkeypatch):
    from meetingbro.main import _llm_resource_mode
    monkeypatch.setenv('MEETINGBRO_LLM_RESOURCE_MODE', 'post_recording')
    assert _llm_resource_mode() == 'post_recording'
    monkeypatch.setenv('MEETINGBRO_LLM_RESOURCE_MODE', 'unexpected')
    assert _llm_resource_mode() == 'balanced'


def test_cancelled_waiting_request_never_starts_later():
    async def run():
        coordinator = InferenceCoordinator()
        coordinator.memory_free_percent = 1
        started = threading.Event()
        task = asyncio.create_task(coordinator.run(lambda: started.set()))
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        coordinator.memory_free_percent = 50
        await asyncio.sleep(0.3)
        assert not started.is_set()
        await coordinator.close()
    asyncio.run(run())


def test_memory_probe_defers_until_first_sample():
    async def run():
        release = threading.Event()
        started = threading.Event()
        def reader():
            release.wait(2)
            return 5.0
        coordinator = InferenceCoordinator(memory_reader=reader, sample_interval=10)
        coordinator.start()
        task = asyncio.create_task(coordinator.run(lambda: started.set()))
        await asyncio.sleep(0.1)
        assert not started.is_set() and coordinator.defer_reason() == 'checking memory pressure'
        release.set()
        await asyncio.sleep(0.3)
        assert not started.is_set() and coordinator.defer_reason() == 'memory pressure'
        coordinator.memory_free_percent = 50
        await asyncio.wait_for(task, 2)
        assert started.is_set()
        await coordinator.close()
    asyncio.run(run())
