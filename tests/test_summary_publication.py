import asyncio
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

from meetingbro.exporter import export_meeting
from meetingbro.reports.generator import ReportGenerationError, generate_report
from meetingbro.reports.live import update_live_report
from meetingbro.schemas import ReportBlock, ReportDocument, ReportRevision, SummarySnapshot, TranscriptSegment
from meetingbro.storage.db import Storage
from meetingbro.summarization.llm import LLMSummarizer


def seed(storage):
    storage.create_meeting('m')
    now = datetime.now(timezone.utc)
    storage.insert_segment(TranscriptSegment(id='s', meeting_id='m', start_time=0, end_time=1,
        text='Discussed the launch.', original_language='en', confidence=1, created_at=now))
    return now


def snapshot(now, method='unknown', kind='rolling_summary'):
    return SummarySnapshot(id=kind, meeting_id='m', summary_type=kind, time_start=0, time_end=1,
        language='en', content='Extracted text', generation_method=method, source_segment_ids=['s'], created_at=now)


def report(now, method):
    return ReportRevision(report_id=method, meeting_id='m', report_type='meeting', revision=1,
        document=ReportDocument(generation_method=method, title='Launch',
            takeaways=[ReportBlock(text='Discussed launch.', source_segment_ids=['s'])]), created_at=now)


@pytest.mark.parametrize('method', ['extractive', 'mixed'])
def test_non_model_reports_and_legacy_snapshots_export_only_transcript(tmp_path, method):
    db = Storage(tmp_path / 'db.sqlite')
    try:
        now = seed(db)
        db.insert_report_revision(report(now, method))
        for kind in ('rolling_summary', 'refined_transcript', 'final_summary'):
            db.insert_snapshot(snapshot(now, kind=kind))
        result = export_meeting(db, meeting_id='m', export_root=tmp_path / 'exports')
        assert set(result.files) == {'transcript.md', 'metadata.json'}
        directory = Path(result.export_dir)
        assert {p.name for p in directory.iterdir()} == set(result.files)
        assert json.loads((directory / 'metadata.json').read_text())['summary_count'] == 0
        assert 'Discussed the launch.' in (directory / 'transcript.md').read_text()
        assert update_live_report(db, snapshot(now)) is None
    finally:
        db.close()


def test_model_provenance_survives_storage_and_filters_history(tmp_path):
    from meetingbro.main import app, list_reports, list_summaries
    db = Storage(tmp_path / 'db.sqlite')
    try:
        now = seed(db)
        db.insert_snapshot(snapshot(now))
        db.insert_snapshot(snapshot(now, 'llm', 'final_summary'))
        db.insert_report_revision(report(now, 'extractive'))
        db.insert_report_revision(report(now, 'mixed'))
        db.insert_report_revision(report(now, 'llm'))
        app.state.storage = db
        assert [s.generation_method for s in asyncio.run(list_summaries('m', None))] == ['llm']
        assert [r.document.generation_method for r in asyncio.run(list_reports('m'))] == ['llm']
        result = export_meeting(db, meeting_id='m', export_root=tmp_path / 'exports')
        assert 'meeting-report.html' in result.files and 'summary.md' in result.files
        assert update_live_report(db, snapshot(now, 'llm')).document.generation_method == 'llm'
    finally:
        db.close()


def test_upgrade_does_not_claim_legacy_snapshots_are_model_generated(tmp_path):
    path = tmp_path / 'old.sqlite'
    db = Storage(path)
    now = seed(db)
    db.insert_snapshot(snapshot(now))
    db.close()
    with sqlite3.connect(path) as conn:
        conn.execute('ALTER TABLE summary_snapshots DROP COLUMN generation_method')
        conn.execute('DELETE FROM meetingbro_migrations WHERE version=4')
    db = Storage(path)
    assert db.list_snapshots('m')[0].generation_method == 'unknown'
    db.close()


def test_unconfigured_or_failed_model_never_returns_extractive_content(monkeypatch):
    summarizer = LLMSummarizer()
    monkeypatch.setattr(summarizer, '_ensure_client', lambda: None)
    # A nonempty input must still produce no summary when the provider is absent.
    segments = [TranscriptSegment(id='s', meeting_id='m', start_time=0, end_time=1,
        text='Discussed the launch.', original_language='en', confidence=1, created_at=datetime.now(timezone.utc))]
    assert summarizer.summarize(segments, kind='rolling_summary', language='en') == ''
    assert 'No summary model' in summarizer.last_error
    with pytest.raises(RuntimeError, match='No summary model'):
        summarizer.finalize_meeting(segments, language='en')

    class Offline:
        def chat(self, **kwargs): raise RuntimeError('offline')
    summarizer._compatible_client = Offline()
    monkeypatch.setattr(summarizer, '_ensure_client', lambda: 'openai_compatible')
    assert summarizer.summarize(segments, kind='rolling_summary', language='en') == ''
    assert summarizer.last_error == 'offline'


def test_one_failed_chunk_rejects_the_entire_report():
    class Client:
        calls = 0
        def chat(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return json.dumps({'blocks': [{'text': 'Launch discussed.', 'source_segment_ids': ['s']}]})
            raise RuntimeError('offline')
    segments = [TranscriptSegment(id='s', meeting_id='m', start_time=0, end_time=1,
        text='a' * 17000, original_language='en', confidence=1, created_at=datetime.now(timezone.utc))]
    with pytest.raises(ReportGenerationError, match='Part 2'):
        generate_report(segments, language='en', client=Client())


def test_duplicate_paid_requests_are_rejected_and_failure_persists_nothing(tmp_path, monkeypatch):
    from meetingbro import main
    from meetingbro.resources.coordinator import InferenceCoordinator
    db = Storage(tmp_path / 'duplicate.sqlite')
    seed(db)
    main.app.state.storage = db
    main.app.state.report_locks = {}
    main.app.state.inference_coordinator = InferenceCoordinator()
    started, release = threading.Event(), threading.Event()
    class Client:
        calls = 0
        def chat(self, **kwargs):
            self.calls += 1
            started.set()
            release.wait(3)
            raise RuntimeError('offline')
    client = Client()
    monkeypatch.setattr(main.OpenAICompatibleClient, 'from_env', classmethod(lambda cls: client))
    async def run():
        first = asyncio.create_task(main.generate_meeting_report('m'))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            with pytest.raises(HTTPException) as duplicate:
                await main.generate_meeting_report('m')
            assert duplicate.value.status_code == 409
        finally:
            release.set()
        with pytest.raises(HTTPException) as failed:
            await first
        assert failed.value.status_code == 502
        assert client.calls == 1 and not db.list_report_revisions('m')
    try:
        asyncio.run(run())
    finally:
        release.set()
        db.close()
