import asyncio
import json

import numpy as np
from fastapi import WebSocketDisconnect

from meetingbro.asr.base import ASRSegment
from meetingbro.audio.capture import AudioChunk, AudioSource
from meetingbro.main import app, session_ws
from meetingbro.session.manager import SessionConfig, SessionManager
from meetingbro.session.registry import SessionRegistry
from meetingbro.storage.db import Storage
from meetingbro.summarization.heuristic import HeuristicSummarizer
from meetingbro.translation.passthrough import PassthroughTranslator


class Source(AudioSource):
    def __init__(self): self.queue = asyncio.Queue()
    @property
    def sample_rate(self): return 16000
    async def stream(self):
        while True:
            item = await self.queue.get()
            if item is None:
                return
            yield item
    async def aclose(self): await self.queue.put(None)


class ASR:
    def __init__(self): self.n = 0
    def transcribe(self, samples, sample_rate, **kwargs):
        self.n += 1
        return [ASRSegment(start_time=0, end_time=len(samples)/sample_rate,
                           text=f'Unique transcript number {self.n}.', language='en', confidence=1)]


class Socket:
    def __init__(self):
        self.messages = []
        self.commands = asyncio.Queue()
        self.code = None
    async def accept(self): pass
    async def send_text(self, text): self.messages.append(json.loads(text))
    async def receive_text(self):
        value = await self.commands.get()
        if value is None: raise WebSocketDisconnect()
        return json.dumps(value)
    async def close(self, code=1000, reason=None): self.code = code


async def invoke(ws, token, reconnect=True):
    await session_ws(ws, source='mic', summary_language='en', forced_language=None,
                     vocabulary_hint=None, runtime_profile='balanced', audio_device=None,
                     loopback_device=None, session_token=token, reconnect=reconnect)


async def until(predicate):
    async def poll():
        while not predicate(): await asyncio.sleep(0.01)
    await asyncio.wait_for(poll(), 3)


def test_reconnect_preserves_meeting_and_replays_snapshot_without_duplicate_events(tmp_path):
    async def run():
        storage = Storage(tmp_path / 'reconnect.sqlite')
        source = Source()
        cfg = SessionConfig(audio_source=source, asr=ASR(), storage=storage,
            summarizer=HeuristicSummarizer(), translator=PassthroughTranslator(),
            pre_vad_enabled=False, fast_preview_enabled=False, asr_accumulation_seconds=0.5,
            formal_incremental_commit=True, final_summary_enabled=False)
        manager = SessionManager(cfg)
        registry = SessionRegistry(grace_seconds=1)
        app.state.sessions, app.state.storage = registry, storage
        token, old = 'a'*32, object()
        registry.reserve(token, old)
        registry.activate(token, old, manager)
        await manager.start()
        samples = (np.sin(2*np.pi*440*np.arange(8000)/16000)*0.03).astype(np.float32)
        try:
            await source.queue.put(AudioChunk(samples=samples, sample_rate=16000, start_time=0))
            await until(lambda: len(storage.list_segments(manager.meeting_id)) == 1)
            registry.detach(token, old)
            await source.queue.put(AudioChunk(samples=samples, sample_rate=16000, start_time=0.5))
            await until(lambda: len(storage.list_segments(manager.meeting_id)) == 2)
            ws = Socket()
            task = asyncio.create_task(invoke(ws, token))
            await until(lambda: bool(ws.messages))
            sync = ws.messages[0]
            assert sync['type'] == 'session_sync'
            assert sync['payload']['state']['meeting_id'] == manager.meeting_id
            assert len(sync['payload']['segments']) == 2
            rival = Socket()
            await invoke(rival, token)
            assert rival.code == 4409
            await source.queue.put(AudioChunk(samples=samples, sample_rate=16000, start_time=1))
            await until(lambda: any(m['type'] == 'transcript_segment' for m in ws.messages))
            segment_events = [m for m in ws.messages if m['type'] == 'transcript_segment']
            assert len(segment_events) == 1 and segment_events[0]['payload']['start_time'] == 1
            await ws.commands.put({'type': 'stop'})
            await asyncio.wait_for(task, 3)
            assert len(storage.list_meetings()) == 1
            assert len(storage.list_segments(manager.meeting_id)) == 3
            assert registry.attach(token, object())[0] == 'missing'
        finally:
            await registry.close()
            if not manager._stopped.is_set(): await manager.stop()
            storage.close()
    asyncio.run(run())


def test_registry_expiry_stops_session_and_reservation_can_be_cancelled():
    class Manager:
        def __init__(self): self.calls = []
        async def stop(self, *, graceful): self.calls.append(graceful)
    async def run():
        registry = SessionRegistry(grace_seconds=0.02)
        owner, next_owner, manager = object(), object(), Manager()
        assert registry.reserve('a', owner)
        assert not registry.reserve('a', next_owner)
        assert await registry.stop('a')
        assert not registry.activate('a', owner, manager)
        assert registry.attach('a', next_owner)[0] == 'missing'
        assert registry.reserve('b', owner)
        assert registry.activate('b', owner, manager)
        registry.detach('b', owner)
        assert registry.attach('b', next_owner) == ('ok', manager)
        registry.detach('b', next_owner)
        await asyncio.sleep(0.05)
        assert manager.calls == [True]
        assert registry.attach('b', owner)[0] == 'missing'
        await registry.close()
    asyncio.run(run())


def test_initial_connection_disconnect_and_reattach_keep_same_session(tmp_path, monkeypatch):
    from meetingbro import main
    from meetingbro.hardware import HardwareProfile

    async def run():
        storage = Storage(tmp_path / 'full.sqlite')
        registry, source = SessionRegistry(grace_seconds=2), Source()
        app.state.sessions, app.state.storage = registry, storage
        from meetingbro.resources.coordinator import InferenceCoordinator
        app.state.inference_coordinator = InferenceCoordinator()
        app.state.asr = ASR()
        app.state.hardware_profile = HardwareProfile(cpu_count=8, cuda_available=False)
        app.state.preview_asr = None
        app.state.preview_asr_backend_name = 'shared'
        app.state.formal_asr_device = 'cpu'
        app.state.asr_engine = 'qwen3'
        class Worker:
            async def enqueue(self, *args): return {}
        app.state.final_summary_worker = Worker()
        monkeypatch.setattr(main, '_build_audio_source', lambda *args, **kwargs: source)
        monkeypatch.setenv('MEETINGBRO_PRE_VAD_ENABLED', 'false')
        monkeypatch.setenv('MEETINGBRO_FINAL_SUMMARY_ENABLED', 'false')
        monkeypatch.setenv('MEETINGBRO_FORMAL_INCREMENTAL_COMMIT', 'true')
        token, first = 'x'*32, Socket()
        task = asyncio.create_task(invoke(first, token, reconnect=False))
        samples = (np.sin(2*np.pi*440*np.arange(8000)/16000)*0.03).astype(np.float32)
        try:
            await until(lambda: any(m['type'] == 'session_state' for m in first.messages))
            manager = registry._entries[token].manager
            meeting_id = manager.meeting_id
            for i in range(4):
                await source.queue.put(AudioChunk(samples=samples, sample_rate=16000, start_time=i*0.5))
            await until(lambda: len(storage.list_segments(meeting_id)) >= 1)
            await first.commands.put(None)
            await asyncio.wait_for(task, 3)
            assert not manager._stopped.is_set()
            for i in range(4, 8):
                await source.queue.put(AudioChunk(samples=samples, sample_rate=16000, start_time=i*0.5))
            await until(lambda: len(storage.list_segments(meeting_id)) >= 2)
            second = Socket()
            reattached = asyncio.create_task(invoke(second, token))
            await until(lambda: bool(second.messages))
            assert second.messages[0]['type'] == 'session_sync'
            assert second.messages[0]['payload']['state']['meeting_id'] == meeting_id
            assert len(second.messages[0]['payload']['segments']) >= 2
            assert len(storage.list_meetings()) == 1
            await second.commands.put({'type': 'stop'})
            await asyncio.wait_for(reattached, 3)
            assert manager._stopped.is_set()
        finally:
            await registry.close()
            storage.close()
    asyncio.run(run())


def test_audio_committed_during_snapshot_send_is_forwarded(tmp_path):
    class DelayedSocket(Socket):
        def __init__(self):
            super().__init__()
            self.snapshot_started = asyncio.Event()
            self.release_snapshot = asyncio.Event()
        async def send_text(self, text):
            message = json.loads(text)
            if message['type'] == 'session_sync':
                self.snapshot_started.set()
                await self.release_snapshot.wait()
            self.messages.append(message)

    async def run():
        storage = Storage(tmp_path / 'race.sqlite')
        source = Source()
        cfg = SessionConfig(audio_source=source, asr=ASR(), storage=storage,
            summarizer=HeuristicSummarizer(), translator=PassthroughTranslator(),
            pre_vad_enabled=False, fast_preview_enabled=False, asr_accumulation_seconds=0.5,
            formal_incremental_commit=True, final_summary_enabled=False)
        manager, registry = SessionManager(cfg), SessionRegistry(grace_seconds=1)
        app.state.sessions, app.state.storage = registry, storage
        token, old = 'z'*32, object()
        registry.reserve(token, old)
        registry.activate(token, old, manager)
        await manager.start()
        registry.detach(token, old)
        ws = DelayedSocket()
        task = asyncio.create_task(invoke(ws, token))
        samples = (np.sin(2*np.pi*440*np.arange(8000)/16000)*0.03).astype(np.float32)
        try:
            await asyncio.wait_for(ws.snapshot_started.wait(), 2)
            await source.queue.put(AudioChunk(samples=samples, sample_rate=16000, start_time=0))
            await until(lambda: len(storage.list_segments(manager.meeting_id)) == 1)
            ws.release_snapshot.set()
            await until(lambda: any(m['type'] == 'transcript_segment' for m in ws.messages))
            assert ws.messages[0]['payload']['segments'] == []
            assert len([m for m in ws.messages if m['type'] == 'transcript_segment']) == 1
            await ws.commands.put({'type': 'stop'})
            await asyncio.wait_for(task, 3)
        finally:
            ws.release_snapshot.set()
            if not task.done(): task.cancel(); await asyncio.gather(task, return_exceptions=True)
            await registry.close()
            if not manager._stopped.is_set(): await manager.stop()
            storage.close()
    asyncio.run(run())


def test_detached_stop_endpoint_stops_only_the_matching_token():
    from fastapi import HTTPException
    from meetingbro.main import StopDetachedSessionRequest, stop_detached_session
    import pytest

    class Manager:
        def __init__(self): self.calls = []
        async def stop(self, *, graceful): self.calls.append(graceful)

    async def run():
        registry = SessionRegistry(grace_seconds=5)
        app.state.sessions = registry
        owner, manager, token = object(), Manager(), 't' * 32
        registry.reserve(token, owner)
        registry.activate(token, owner, manager)
        registry.detach(token, owner)
        with pytest.raises(HTTPException) as missing:
            await stop_detached_session(StopDetachedSessionRequest(session_token='u' * 32))
        assert missing.value.status_code == 404 and not manager.calls
        assert await stop_detached_session(StopDetachedSessionRequest(session_token=token)) == {'stopped': True}
        assert manager.calls == [True]
        assert registry.attach(token, object())[0] == 'missing'
        await registry.close()
    asyncio.run(run())
