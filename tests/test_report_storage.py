from datetime import datetime, timezone
import sqlite3

import pytest

from meetingbro.schemas import ReportBlock, ReportDocument, ReportRevision, TranscriptSegment
from meetingbro.storage.db import Storage


def test_report_upgrade_revisions_and_citations(tmp_path):
    path = tmp_path / 'old.db'
    db = Storage(path)
    db.create_meeting('one')
    db.create_meeting('two')
    now = datetime.now(timezone.utc)
    db.insert_segment(TranscriptSegment(id='s', meeting_id='one', start_time=0, end_time=1,
                                        text='hello', original_language='en', confidence=1, created_at=now))
    db.close()
    db = Storage(path)
    report = ReportRevision(report_id='r', meeting_id='one', report_type='meeting', revision=1,
                            document=ReportDocument(takeaways=[ReportBlock(text='hello', source_segment_ids=['s'])]),
                            created_at=now)
    db.insert_report_revision(report)
    with pytest.raises(ValueError):
        db.insert_report_revision(report)
    with pytest.raises(ValueError):
        db.insert_report_revision(report.model_copy(update={'revision': 2, 'meeting_id': 'two'}))
    db.insert_report_revision(report.model_copy(update={'revision': 2}))
    assert [r.revision for r in db.list_report_revisions('one')] == [1, 2]
    db.delete_meeting('one')
    assert db.list_report_revisions('one') == []
    db.close()


def test_migration_rolls_back_on_conflicting_table(tmp_path):
    path = tmp_path / 'broken.db'
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE report_revisions (other TEXT)')
    conn.commit()
    conn.close()
    with pytest.raises(sqlite3.OperationalError):
        Storage(path)
    conn = sqlite3.connect(path)
    assert not conn.execute("SELECT name FROM sqlite_master WHERE name = 'meetingbro_migrations'").fetchone()
    conn.close()


def test_report_export_escapes_untrusted_text(tmp_path):
    from meetingbro.exporter import export_meeting

    db = Storage(tmp_path / 'db.sqlite')
    db.create_meeting('one')
    report = ReportRevision(report_id='r', meeting_id='one', report_type='meeting', revision=1,
                            document=ReportDocument(generation_method='llm', title='<script>alert(1)</script>',
                                takeaways=[ReportBlock(text='<img src=x onerror=alert(1)>')]),
                            created_at=datetime.now(timezone.utc))
    db.insert_report_revision(report)
    result = export_meeting(db, meeting_id='one', export_root=tmp_path / 'exports')
    html = (tmp_path / 'exports' / __import__('pathlib').Path(result.export_dir).name / 'meeting-report.html').read_text()
    assert 'meeting-report.html' in result.files
    assert html.count('<script>') == 1 and '<img' not in html
    assert '&lt;script&gt;' in html and '&lt;img' in html
    db.close()
