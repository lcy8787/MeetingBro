"""Single-worker, restartable final summary jobs."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import uuid
from datetime import datetime, timezone
from typing import Callable

from ..reports.live import update_live_report
from ..resources.coordinator import InferenceCoordinator
from ..schemas import SummarySnapshot
from ..storage.db import Storage
from ..summarization.base import Summarizer

logger = logging.getLogger(__name__)


def _snapshots(meeting_id: str, language: str, segments, result: dict) -> list[SummarySnapshot]:
    if not isinstance(result, dict) or not isinstance(result.get('final_summary'), str) or not result['final_summary'].strip():
        raise ValueError('Final summary is empty or invalid')
    end = max(segment.end_time for segment in segments)
    chapters = []
    for item in result.get('chapters', []):
        if not isinstance(item, dict):
            continue
        try:
            start, finish = float(item.get('time_start', 0)), float(item.get('time_end', end))
            if not math.isfinite(start) or not math.isfinite(finish):
                continue
        except (TypeError, ValueError):
            continue
        start = max(0.0, min(start, end))
        finish = max(start, min(finish, end))
        chapters.append({'title': str(item.get('title') or 'Untitled chapter'), 'time_start': start,
                         'time_end': finish, 'summary': str(item.get('summary') or '')})
    actions = []
    for item in result.get('action_items', []):
        if not isinstance(item, dict) or not isinstance(item.get('text'), str) or not item['text'].strip():
            continue
        actions.append({'text': item['text'].strip(),
                        'assignee': str(item['assignee']) if item.get('assignee') else None,
                        'due': str(item['due']) if item.get('due') else None})
    now = datetime.now(timezone.utc)
    sources = [segment.id for segment in segments]
    return [SummarySnapshot(id=str(uuid.uuid4()), meeting_id=meeting_id, summary_type=kind,
                            time_start=0, time_end=end, language=language, content=content,
                            source_segment_ids=sources, is_latest=True, created_at=now)
            for kind, content in (('final_summary', result['final_summary'].strip()),
                                  ('chapter_list', json.dumps(chapters, ensure_ascii=False)),
                                  ('action_item_list', json.dumps(actions, ensure_ascii=False)))]


class FinalSummaryWorker:
    def __init__(self, storage: Storage, summarizer_factory: Callable[[], Summarizer],
                 coordinator: InferenceCoordinator | None = None):
        self.storage = storage
        self._factory = summarizer_factory
        self._coordinator = coordinator
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self.storage.recover_final_summary_jobs()
        self._task = asyncio.create_task(self._run())
        self._wake.set()

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def enqueue(self, meeting_id: str, language: str, vocabulary: str | None = None) -> dict:
        job = self.storage.queue_final_summary(str(uuid.uuid4()), meeting_id, language, vocabulary)
        self._wake.set()
        return job

    async def _run(self) -> None:
        while True:
            self._wake.clear()
            if (self._coordinator is not None and self._coordinator.waiting_reason() is not None
                    and self.storage.has_queued_final_summary_job()):
                await asyncio.sleep(0.25)
                continue
            job = self.storage.claim_final_summary_job()
            if job is None:
                await self._wake.wait()
                continue
            try:
                segments = self.storage.list_segments(job['meeting_id'])
                if not segments:
                    raise ValueError('Meeting has no saved transcript')
                memory = self.storage.list_snapshots(job['meeting_id'], 'meeting_memory')
                previous = memory[-1].content if memory else None
                summarizer = self._factory()
                def finalize():
                    # An unconfigured provider is a retryable failure, not a successful LLM job.
                    ensure = getattr(summarizer, '_ensure_client', None)
                    if ensure is not None and ensure() is None:
                        raise RuntimeError('No summary model is configured')
                    return summarizer.finalize_meeting(segments, language=job['language'],
                                                       vocabulary=job['vocabulary'], meeting_memory=previous)
                result = (await self._coordinator.run(finalize) if self._coordinator is not None
                          else await asyncio.to_thread(finalize))
                snapshots = _snapshots(job['meeting_id'], job['language'], segments, result)
                if self.storage.complete_final_summary_job(job['id'], snapshots):
                    try:
                        update_live_report(self.storage, snapshots[0])
                    except Exception:
                        logger.exception('Final summary saved but report update failed')
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning('Final summary job failed: %s', exc)
                self.storage.fail_final_summary_job(job['id'], f'{type(exc).__name__}: {exc}')
