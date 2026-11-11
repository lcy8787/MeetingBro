import sqlite3
from datetime import datetime, timezone

import pytest

from meetingbro.schemas import TranscriptSegment
from meetingbro.storage.db import SCHEMA, Storage


def test_legacy_database_upgrades_and_reopens_without_changing_old_transcript(tmp_path):
    path = tmp_path / 'old.sqlite'
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO meetings(id,started_at) VALUES ('m','2026-01-01T00:00:00+00:00')")
    conn.execute("INSERT INTO transcript_segments(id,meeting_id,start_time,end_time,text,original_language,confidence,created_at) VALUES ('s','m',0,1,'old text','en',1,'2026-01-01T00:00:00+00:00')")
    conn.commit()
    conn.close()
    db = Storage(path)
    row = db.list_segments('m')[0]
    assert (row.text, row.revision, row.sequence, row.status) == ('old text', 1, None, 'final')
    db.close()
    db = Storage(path)
    assert db.list_segments('m')[0].text == 'old text'
    db.close()


def test_version_migration_rolls_back_when_a_column_conflicts(tmp_path):
    path = tmp_path / 'broken.sqlite'
    db = Storage(path)
    db.close()
    conn = sqlite3.connect(path)
    conn.execute('DELETE FROM meetingbro_migrations WHERE version = 2')
    conn.execute('ALTER TABLE transcript_segments DROP COLUMN sequence')
    conn.commit()
    conn.close()
    with pytest.raises(sqlite3.OperationalError):
        Storage(path)
    conn = sqlite3.connect(path)
    assert not conn.execute('SELECT 1 FROM meetingbro_migrations WHERE version = 2').fetchone()
    assert 'sequence' not in {row[1] for row in conn.execute('PRAGMA table_info(transcript_segments)')}
    conn.close()
