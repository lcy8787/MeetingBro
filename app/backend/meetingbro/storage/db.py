from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from ..schemas import Note, ReportDocument, ReportRevision, Speaker, SummarySnapshot, SummaryType, TranscriptSegment


SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
  id TEXT PRIMARY KEY,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  preferred_summary_language TEXT NOT NULL DEFAULT 'en'
);

CREATE TABLE IF NOT EXISTS transcript_segments (
  id TEXT PRIMARY KEY,
  meeting_id TEXT NOT NULL,
  start_time REAL NOT NULL,
  end_time REAL NOT NULL,
  text TEXT NOT NULL,
  original_language TEXT NOT NULL,
  speaker_id TEXT,
  confidence REAL NOT NULL,
    quality TEXT NOT NULL DEFAULT 'ok',
  translations TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS summary_snapshots (
  id TEXT PRIMARY KEY,
  meeting_id TEXT NOT NULL,
  summary_type TEXT NOT NULL,
  time_start REAL NOT NULL,
  time_end REAL NOT NULL,
  language TEXT NOT NULL,
  content TEXT NOT NULL,
  source_segment_ids TEXT NOT NULL DEFAULT '[]',
  is_latest INTEGER NOT NULL DEFAULT 0,
  translations TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
  id TEXT PRIMARY KEY,
  meeting_id TEXT NOT NULL,
  content TEXT NOT NULL,
  source_type TEXT,
  source_id TEXT,
    time_seconds REAL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS speakers (
  id TEXT PRIMARY KEY,
  meeting_id TEXT NOT NULL,
  display_name TEXT,
  inferred_label TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 0.5,
  is_local_user INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_segments_meeting ON transcript_segments(meeting_id, start_time);
CREATE INDEX IF NOT EXISTS idx_snapshots_meeting_type ON summary_snapshots(meeting_id, summary_type, created_at);
CREATE INDEX IF NOT EXISTS idx_notes_meeting ON notes(meeting_id, created_at);
CREATE INDEX IF NOT EXISTS idx_speakers_meeting ON speakers(meeting_id);
"""


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


class Storage:
    """Thin sync SQLite wrapper. Guarded by an internal lock so it is safe to call
    from both the WebSocket loop and background worker threads.
    """

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        try:
            with self._lock:
                self._conn.executescript(SCHEMA)
                try:
                    self._conn.execute("ALTER TABLE transcript_segments ADD COLUMN quality TEXT NOT NULL DEFAULT 'ok'")
                except sqlite3.OperationalError:
                    pass
                try:
                    self._conn.execute("ALTER TABLE notes ADD COLUMN time_seconds REAL")
                    self._conn.commit()
                except sqlite3.OperationalError:
                    pass
                self._conn.commit()
                self._migrate_reports()

        except Exception:
            self._conn.close()
            raise

    def _migrate_reports(self) -> None:
        # Keep our own migration ledger so existing databases' user_version is untouched.
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._conn.execute("CREATE TABLE IF NOT EXISTS meetingbro_migrations (version INTEGER PRIMARY KEY)")
            if not self._conn.execute("SELECT 1 FROM meetingbro_migrations WHERE version = 1").fetchone():
                self._conn.execute("""CREATE TABLE report_revisions (
                    report_id TEXT NOT NULL, meeting_id TEXT NOT NULL,
                    report_type TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision > 0),
                    document TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY (report_id, revision),
                    FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
                )""")
                self._conn.execute("CREATE INDEX idx_reports_meeting ON report_revisions(meeting_id, report_type, revision)")
                self._conn.execute("INSERT INTO meetingbro_migrations(version) VALUES (1)")
            if not self._conn.execute("SELECT 1 FROM meetingbro_migrations WHERE version = 2").fetchone():
                self._conn.execute("ALTER TABLE transcript_segments ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
                self._conn.execute("ALTER TABLE transcript_segments ADD COLUMN sequence INTEGER")
                self._conn.execute("INSERT INTO meetingbro_migrations(version) VALUES (2)")
            if not self._conn.execute("SELECT 1 FROM meetingbro_migrations WHERE version = 3").fetchone():
                self._conn.execute("""CREATE TABLE final_summary_jobs (
                    id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL CHECK(status IN ('queued', 'running', 'succeeded', 'failed')),
                    language TEXT NOT NULL, vocabulary TEXT, attempts INTEGER NOT NULL DEFAULT 0,
                    error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    FOREIGN KEY(meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
                )""")
                self._conn.execute("INSERT INTO meetingbro_migrations(version) VALUES (3)")
            if not self._conn.execute("SELECT 1 FROM meetingbro_migrations WHERE version = 4").fetchone():
                self._conn.execute("ALTER TABLE summary_snapshots ADD COLUMN generation_method TEXT NOT NULL DEFAULT 'unknown'")
                self._conn.execute("INSERT INTO meetingbro_migrations(version) VALUES (4)")
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def insert_report_revision(self, report: ReportRevision) -> None:
        """Append a revision after checking that every cited segment belongs to the meeting."""
        blocks = [*report.document.takeaways, *(b for s in report.document.sections for b in s.blocks)]
        citations = {source for block in blocks for source in block.source_segment_ids}
        citations.update(source for block in blocks for option in block.options for source in option.source_segment_ids)
        citations.update(report.document.executive_summary_source_segment_ids)
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                valid = {row[0] for row in self._conn.execute(
                    "SELECT id FROM transcript_segments WHERE meeting_id = ?", (report.meeting_id,)
                )}
                if not citations <= valid:
                    raise ValueError("Report references segments outside this meeting or missing segments")
                previous = self._conn.execute(
                    "SELECT meeting_id, report_type, MAX(revision) FROM report_revisions WHERE report_id = ?",
                    (report.report_id,),
                ).fetchone()
                if previous[2] is not None and (previous[0] != report.meeting_id or previous[1] != report.report_type):
                    raise ValueError("Report identity cannot change between revisions")
                if report.revision != (previous[2] or 0) + 1:
                    raise ValueError("Report revisions must be consecutive")
                self._conn.execute(
                    "INSERT INTO report_revisions VALUES (?, ?, ?, ?, ?, ?)",
                    (report.report_id, report.meeting_id, report.report_type, report.revision,
                     report.document.model_dump_json(), _iso(report.created_at)),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def list_report_revisions(self, meeting_id: str) -> list[ReportRevision]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT report_id, meeting_id, report_type, revision, document, created_at "
                "FROM report_revisions WHERE meeting_id = ? ORDER BY report_id, revision", (meeting_id,)
            ).fetchall()
        return [ReportRevision(report_id=r[0], meeting_id=r[1], report_type=r[2], revision=r[3],
                               document=ReportDocument.model_validate_json(r[4]),
                               created_at=datetime.fromisoformat(r[5])) for r in rows]

    def queue_final_summary(self, job_id: str, meeting_id: str, language: str, vocabulary: str | None) -> dict:
        now = _iso(_utc_now())
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._conn.execute("""INSERT INTO final_summary_jobs
                    (id, meeting_id, status, language, vocabulary, created_at, updated_at)
                    VALUES (?, ?, 'queued', ?, ?, ?, ?)
                    ON CONFLICT(meeting_id) DO UPDATE SET status='queued', language=excluded.language,
                        vocabulary=excluded.vocabulary, error=NULL, updated_at=excluded.updated_at
                    WHERE final_summary_jobs.status IN ('failed', 'succeeded')""",
                    (job_id, meeting_id, language, vocabulary, now, now))
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        result = self.get_final_summary_job(meeting_id)
        assert result is not None
        return result

    def get_final_summary_job(self, meeting_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute("SELECT id, meeting_id, status, language, vocabulary, attempts, error, created_at, updated_at FROM final_summary_jobs WHERE meeting_id = ?", (meeting_id,)).fetchone()
        return dict(zip(('id', 'meeting_id', 'status', 'language', 'vocabulary', 'attempts', 'error', 'created_at', 'updated_at'), row)) if row else None

    def record_incomplete_audio_job(self, job_id: str, meeting_id: str, language: str, vocabulary: str | None) -> None:
        now = _iso(_utc_now())
        with self._lock:
            self._conn.execute("""INSERT INTO final_summary_jobs
                (id, meeting_id, status, language, vocabulary, error, created_at, updated_at)
                VALUES (?, ?, 'failed', ?, ?, ?, ?, ?) ON CONFLICT(meeting_id) DO NOTHING""",
                (job_id, meeting_id, language, vocabulary,
                 'Audio processing timed out. The saved transcript may be incomplete; review it before retrying.', now, now))
            self._conn.commit()

    def recover_final_summary_jobs(self) -> None:
        with self._lock:
            self._conn.execute("UPDATE final_summary_jobs SET status='queued', updated_at=? WHERE status='running'", (_iso(_utc_now()),))
            self._conn.commit()

    def suspend_pending_final_summary_jobs(self) -> None:
        """Require an explicit retry for old jobs when opening the summary UI."""
        with self._lock:
            self._conn.execute(
                "UPDATE final_summary_jobs SET status='failed', error=?, updated_at=? WHERE status IN ('queued', 'running')",
                ('Automatic summary is off. Generate a summary or explicitly retry this job.', _iso(_utc_now())))
            self._conn.commit()

    def claim_final_summary_job(self) -> Optional[dict]:
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._conn.execute("SELECT id, meeting_id, language, vocabulary FROM final_summary_jobs WHERE status='queued' ORDER BY created_at, id LIMIT 1").fetchone()
                if row:
                    self._conn.execute("UPDATE final_summary_jobs SET status='running', attempts=attempts+1, updated_at=? WHERE id=?", (_iso(_utc_now()), row[0]))
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return dict(zip(('id', 'meeting_id', 'language', 'vocabulary'), row)) if row else None

    def has_queued_final_summary_job(self) -> bool:
        with self._lock:
            return self._conn.execute("SELECT 1 FROM final_summary_jobs WHERE status='queued' LIMIT 1").fetchone() is not None

    def fail_final_summary_job(self, job_id: str, error: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE final_summary_jobs SET status='failed', error=?, updated_at=? WHERE id=? AND status='running'", (error[:500], _iso(_utc_now()), job_id))
            self._conn.commit()

    def complete_final_summary_job(self, job_id: str, snapshots: list[SummarySnapshot], report: ReportRevision | None = None) -> bool:
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._conn.execute("SELECT meeting_id FROM final_summary_jobs WHERE id=? AND status='running'", (job_id,)).fetchone()
                if row is None:
                    self._conn.rollback()
                    return False
                if report is not None:
                    if report.meeting_id != row[0] or report.revision != 1:
                        raise ValueError("Invalid final report identity")
                    blocks = [*report.document.takeaways, *(block for section in report.document.sections for block in section.blocks)]
                    citations = {key for block in blocks for key in block.source_segment_ids}
                    citations.update(key for block in blocks for option in block.options for key in option.source_segment_ids)
                    citations.update(report.document.executive_summary_source_segment_ids)
                    valid = {r[0] for r in self._conn.execute("SELECT id FROM transcript_segments WHERE meeting_id=?", (row[0],))}
                    if not citations <= valid:
                        raise ValueError("Final report references missing sources")
                    self._conn.execute("INSERT INTO report_revisions VALUES (?, ?, ?, ?, ?, ?)",
                        (report.report_id, report.meeting_id, report.report_type, report.revision,
                         report.document.model_dump_json(), _iso(report.created_at)))
                for snap in snapshots:
                    if snap.meeting_id != row[0]:
                        raise ValueError("Snapshot does not belong to this job")
                    self._conn.execute("UPDATE summary_snapshots SET is_latest=0 WHERE meeting_id=? AND summary_type=?", (snap.meeting_id, snap.summary_type))
                    self._conn.execute("""INSERT INTO summary_snapshots
                        (id, meeting_id, summary_type, time_start, time_end, language, content, source_segment_ids, is_latest, translations, created_at, generation_method)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (snap.id, snap.meeting_id, snap.summary_type, snap.time_start, snap.time_end, snap.language, snap.content,
                         json.dumps(snap.source_segment_ids), 1, json.dumps(snap.translations), _iso(snap.created_at), snap.generation_method))
                self._conn.execute("UPDATE final_summary_jobs SET status='succeeded', error=NULL, updated_at=? WHERE id=?", (_iso(_utc_now()), job_id))
                self._conn.commit()
                return True
            except Exception:
                self._conn.rollback()
                raise

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def create_meeting(self, meeting_id: str, preferred_summary_language: str = "en") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO meetings (id, started_at, preferred_summary_language) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET started_at=excluded.started_at, ended_at=NULL, "
                "preferred_summary_language=excluded.preferred_summary_language",
                (meeting_id, _iso(_utc_now()), preferred_summary_language),
            )
            self._conn.commit()

    def end_meeting(self, meeting_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE meetings SET ended_at = ? WHERE id = ?",
                (_iso(_utc_now()), meeting_id),
            )
            self._conn.commit()

    def update_meeting_summary_language(self, meeting_id: str, preferred_summary_language: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE meetings SET preferred_summary_language = ? WHERE id = ?",
                (preferred_summary_language, meeting_id),
            )
            self._conn.commit()

    def get_meeting(self, meeting_id: str) -> Optional[dict[str, str | None]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, started_at, ended_at, preferred_summary_language FROM meetings WHERE id = ?",
                (meeting_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "started_at": row[1],
            "ended_at": row[2],
            "preferred_summary_language": row[3],
        }

    def list_meetings(self, limit: int = 100) -> list[dict[str, object]]:
        """Recent meetings (newest first) with a segment count and first-line preview.

        Used by the meeting-history UI. Counts and previews are derived with
        correlated subqueries so a single round-trip returns everything the list
        needs without loading full transcripts.
        """
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT
                  m.id,
                  m.started_at,
                  m.ended_at,
                  m.preferred_summary_language,
                  (SELECT COUNT(*) FROM transcript_segments s WHERE s.meeting_id = m.id) AS segment_count,
                  (SELECT s2.text FROM transcript_segments s2 WHERE s2.meeting_id = m.id
                     ORDER BY s2.start_time ASC LIMIT 1) AS first_text
                FROM meetings m
                ORDER BY m.started_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "id": r[0],
                "started_at": r[1],
                "ended_at": r[2],
                "preferred_summary_language": r[3],
                "segment_count": r[4],
                "first_text": r[5],
            }
            for r in rows
        ]

    def delete_meeting(self, meeting_id: str) -> None:
        """Delete a meeting and all of its child rows (transcript, summaries, notes, speakers)."""
        with self._lock, self._conn:
            for table in ("transcript_segments", "summary_snapshots", "notes", "speakers"):
                self._conn.execute(f"DELETE FROM {table} WHERE meeting_id = ?", (meeting_id,))
            self._conn.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))
            self._conn.commit()

    def insert_segment(self, seg: TranscriptSegment) -> None:
        if seg.status != "final":
            raise ValueError("Only final transcript segments may be persisted")
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO transcript_segments
                    (id, meeting_id, start_time, end_time, text, original_language, speaker_id, confidence, quality, translations, created_at, revision, sequence)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET start_time=excluded.start_time, end_time=excluded.end_time,
                    text=excluded.text, original_language=excluded.original_language, speaker_id=excluded.speaker_id,
                    confidence=excluded.confidence, quality=excluded.quality, translations=excluded.translations,
                    created_at=excluded.created_at, revision=excluded.revision, sequence=excluded.sequence
                WHERE excluded.meeting_id = transcript_segments.meeting_id AND excluded.revision > transcript_segments.revision
                """,
                (
                    seg.id,
                    seg.meeting_id,
                    seg.start_time,
                    seg.end_time,
                    seg.text,
                    seg.original_language,
                    seg.speaker_id,
                    seg.confidence,
                    seg.quality,
                    json.dumps(dict(seg.translations)),
                    _iso(seg.created_at),
                    seg.revision,
                    seg.sequence,
                ),
            )
            self._conn.commit()

    def delete_segment(self, segment_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM transcript_segments WHERE id = ?", (segment_id,))
            self._conn.commit()

    def update_segment_translations(self, segment_id: str, translations: dict[str, str]) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE transcript_segments SET translations = ? WHERE id = ?",
                (json.dumps(dict(translations)), segment_id),
            )
            self._conn.commit()

    def list_segments(self, meeting_id: str) -> list[TranscriptSegment]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, meeting_id, start_time, end_time, text, original_language, speaker_id, confidence, quality, translations, created_at, revision, sequence
                FROM transcript_segments WHERE meeting_id = ? ORDER BY start_time ASC, sequence ASC, id ASC
                """,
                (meeting_id,),
            ).fetchall()
        return [
            TranscriptSegment(
                id=r[0],
                meeting_id=r[1],
                start_time=r[2],
                end_time=r[3],
                text=r[4],
                original_language=r[5],
                speaker_id=r[6],
                confidence=r[7],
                quality=r[8],
                translations=json.loads(r[9] or "{}"),
                created_at=datetime.fromisoformat(r[10]),
                revision=r[11],
                sequence=r[12],
            )
            for r in rows
        ]

    def insert_snapshot(self, snap: SummarySnapshot) -> None:
        with self._lock:
            # Clear is_latest on any prior snapshot of the same type for this meeting.
            self._conn.execute(
                "UPDATE summary_snapshots SET is_latest = 0 WHERE meeting_id = ? AND summary_type = ?",
                (snap.meeting_id, snap.summary_type),
            )
            self._conn.execute(
                """
                INSERT OR REPLACE INTO summary_snapshots
                  (id, meeting_id, summary_type, time_start, time_end, language, content,
                   source_segment_ids, is_latest, translations, created_at, generation_method)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snap.id,
                    snap.meeting_id,
                    snap.summary_type,
                    snap.time_start,
                    snap.time_end,
                    snap.language,
                    snap.content,
                    json.dumps(list(snap.source_segment_ids)),
                    1 if snap.is_latest else 0,
                    json.dumps(dict(snap.translations)),
                    _iso(snap.created_at),
                    snap.generation_method,
                ),
            )
            self._conn.commit()

    def list_snapshots(
        self,
        meeting_id: str,
        summary_type: Optional[SummaryType] = None,
    ) -> list[SummarySnapshot]:
        query = (
            "SELECT id, meeting_id, summary_type, time_start, time_end, language, content, "
            "source_segment_ids, is_latest, translations, created_at, generation_method FROM summary_snapshots "
            "WHERE meeting_id = ?"
        )
        params: list[object] = [meeting_id]
        if summary_type is not None:
            query += " AND summary_type = ?"
            params.append(summary_type)
        query += " ORDER BY created_at ASC"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [
            SummarySnapshot(
                id=r[0],
                meeting_id=r[1],
                summary_type=r[2],
                time_start=r[3],
                time_end=r[4],
                language=r[5],
                content=r[6],
                source_segment_ids=json.loads(r[7] or "[]"),
                is_latest=bool(r[8]),
                translations=json.loads(r[9] or "{}"),
                created_at=datetime.fromisoformat(r[10]),
                generation_method=r[11],
            )
            for r in rows
        ]

    def insert_note(self, note: Note) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO notes (id, meeting_id, content, source_type, source_id, time_seconds, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    note.id,
                    note.meeting_id,
                    note.content,
                    note.source_type,
                    note.source_id,
                    note.time_seconds,
                    _iso(note.created_at),
                ),
            )
            self._conn.commit()

    def list_notes(self, meeting_id: str) -> list[Note]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, meeting_id, content, source_type, source_id, time_seconds, created_at "
                "FROM notes WHERE meeting_id = ? ORDER BY created_at ASC",
                (meeting_id,),
            ).fetchall()
        return [
            Note(
                id=r[0],
                meeting_id=r[1],
                content=r[2],
                source_type=r[3],
                source_id=r[4],
                time_seconds=r[5],
                created_at=datetime.fromisoformat(r[6]),
            )
            for r in rows
        ]

    def bulk_insert_segments(self, segments: Iterable[TranscriptSegment]) -> None:
        for s in segments:
            self.insert_segment(s)

    def upsert_speaker(self, speaker: Speaker) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO speakers
                  (id, meeting_id, display_name, inferred_label, confidence, is_local_user)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    speaker.id,
                    speaker.meeting_id,
                    speaker.display_name,
                    speaker.inferred_label,
                    speaker.confidence,
                    1 if speaker.is_local_user else 0,
                ),
            )
            self._conn.commit()

    def list_speakers(self, meeting_id: str) -> list[Speaker]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, meeting_id, display_name, inferred_label, confidence, is_local_user "
                "FROM speakers WHERE meeting_id = ?",
                (meeting_id,),
            ).fetchall()
        return [
            Speaker(
                id=r[0],
                meeting_id=r[1],
                display_name=r[2],
                inferred_label=r[3],
                confidence=r[4],
                is_local_user=bool(r[5]),
            )
            for r in rows
        ]
