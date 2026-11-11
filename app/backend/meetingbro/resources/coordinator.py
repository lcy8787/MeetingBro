"""Bound concurrent LLM inference and defer new work under capture pressure."""
from __future__ import annotations

import asyncio
from concurrent.futures import Executor
import logging
import platform
import re
import subprocess
import weakref
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)
T = TypeVar('T')


def available_memory_percent() -> float | None:
    """Read OS-level memory availability; unknown readings never claim safety."""
    try:
        if platform.system() == 'Darwin':
            result = subprocess.run(['/usr/bin/memory_pressure', '-Q'], capture_output=True,
                                    text=True, timeout=2, check=True)
            match = re.search(r'System-wide memory free percentage:\s*(\d+)%', result.stdout)
            return float(match.group(1)) if match else None
        if platform.system() == 'Linux':
            values = {}
            with open('/proc/meminfo', encoding='ascii') as stream:
                for line in stream:
                    key, _, value = line.partition(':')
                    if key in ('MemTotal', 'MemAvailable'):
                        values[key] = int(value.strip().split()[0])
            if values.get('MemTotal') and 'MemAvailable' in values:
                return values['MemAvailable'] * 100 / values['MemTotal']
    except (OSError, ValueError, subprocess.SubprocessError):
        logger.debug('Memory pressure is unavailable', exc_info=True)
    return None


class InferenceCoordinator:
    def __init__(self, *, minimum_free_percent: float = 10.0, backlog_seconds: float = 0.5,
                 sample_interval: float = 5.0, memory_reader: Callable[[], float | None] = available_memory_percent):
        self._lock = asyncio.Lock()
        self._sessions = weakref.WeakSet()
        self._minimum_free_percent = minimum_free_percent
        self._backlog_seconds = backlog_seconds
        self._sample_interval = sample_interval
        self._memory_reader = memory_reader
        self.memory_free_percent: float | None = None
        self._sampled = False
        self._sampler: asyncio.Task | None = None
        self._inflight: set[asyncio.Task] = set()

    def start(self) -> None:
        if self._sampler is None:
            self._sampler = asyncio.create_task(self._sample_memory())

    async def close(self) -> None:
        if self._sampler is not None:
            self._sampler.cancel()
            await asyncio.gather(self._sampler, return_exceptions=True)
            self._sampler = None
        # Inference already dispatched to a thread cannot safely be cancelled.
        # Its detached task retains the lock until the thread finishes.

    def register(self, session) -> None:
        self._sessions.add(session)

    def unregister(self, session) -> None:
        self._sessions.discard(session)

    async def _sample_memory(self) -> None:
        while True:
            try:
                self.memory_free_percent = await asyncio.to_thread(self._memory_reader)
            except Exception:
                logger.exception('Could not sample memory pressure')
                self.memory_free_percent = None
            self._sampled = True
            await asyncio.sleep(self._sample_interval)

    def defer_reason(self) -> str | None:
        if self._sampler is not None and not self._sampled:
            return 'checking memory pressure'
        if self.memory_free_percent is not None and self.memory_free_percent < self._minimum_free_percent:
            return 'memory pressure'
        for session in list(self._sessions):
            task = session._task
            if task is not None and task.done():
                continue
            state = session._state
            if state.audio_input_backlog_seconds > self._backlog_seconds:
                return 'audio backlog'
            if state.asr_inflight_start_wall_time is not None or state.fast_preview_inflight:
                return 'ASR inference'
        return None

    def waiting_reason(self) -> str | None:
        return self.defer_reason() or ('another model request' if self._lock.locked() else None)

    async def run(self, fn: Callable[[], T], *, executor: Executor | None = None) -> T:
        """Run once when safe; cancellation never starts another concurrent model call."""
        started = False
        async def guarded() -> T:
            nonlocal started
            while True:
                while self.defer_reason() is not None:
                    await asyncio.sleep(0.25)
                async with self._lock:
                    if self.defer_reason() is not None:
                        continue
                    started = True
                    return await asyncio.get_running_loop().run_in_executor(executor, fn)
        task = asyncio.create_task(guarded())
        self._inflight.add(task)
        def finished(done: asyncio.Task) -> None:
            self._inflight.discard(done)
            if not done.cancelled():
                done.exception()  # consume an exception even when the original caller disconnected
        task.add_done_callback(finished)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if not started:
                task.cancel()
            raise
