import { t, useI18n, uiLocale } from "../i18n";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  deleteMeeting,
  exportMeetingById,
  fetchMeetings,
  fetchMeetingNotes,
  fetchFinalSummaryJob,
  fetchMeetingReports,
  fetchMeetingSummaries,
  fetchMeetingTranscript,
  generateMeetingReport,
  retryFinalSummaryJob,
  type MeetingListItem,
} from "../system/backendApi";
import type { ExportMeetingResponse, FinalSummaryJob, Note, ReportRevision, SummarySnapshot, TranscriptSegment } from "../types";
import { ExportLocationActions } from "./ExportLocationActions";
import { ReportView } from "./ReportView";

interface HistoryModalProps {
  open: boolean;
  onClose: () => void;
  currentMeetingId?: string | null;
}

interface MeetingDetail {
  transcript: TranscriptSegment[];
  summaries: SummarySnapshot[];
  notes: Note[];
  reports: ReportRevision[];
  finalJob: FinalSummaryJob | null;
}

// Most-useful cumulative summary to show as the meeting overview, best first.
const OVERVIEW_PRIORITY: SummarySnapshot["summary_type"][] = [
  "final_summary",
  "cumulative_meeting_summary",
  "rolling_summary",
];

function formatStartedAt(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(uiLocale(), {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatDuration(startedAt: string, endedAt: string | null): string | null {
  if (!endedAt) return null;
  const start = new Date(startedAt).getTime();
  const end = new Date(endedAt).getTime();
  if (Number.isNaN(start) || Number.isNaN(end) || end <= start) return null;
  const totalSeconds = Math.round((end - start) / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return minutes > 0 ? `${minutes}m ${seconds}s` : `${seconds}s`;
}

function pickOverview(summaries: SummarySnapshot[]): SummarySnapshot | null {
  for (const type of OVERVIEW_PRIORITY) {
    const match = summaries.filter((s) => s.summary_type === type).at(-1);
    if (match) return match;
  }
  return summaries.at(-1) ?? null;
}

export function HistoryModal({ open, onClose, currentMeetingId }: HistoryModalProps) {
  useI18n();
  const [meetings, setMeetings] = useState<MeetingListItem[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<MeetingDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [actionNote, setActionNote] = useState<string | null>(null);
  const [exported, setExported] = useState<ExportMeetingResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const selectedIdRef = useRef<string | null>(null);

  const loadList = useCallback(() => {
    setListError(null);
    fetchMeetings()
      .then(setMeetings)
      .catch((err) => {
        setMeetings([]);
        setListError((err as Error).message);
      });
  }, []);

  useEffect(() => {
    if (!open) return;
    setSelectedId(null);
    selectedIdRef.current = null;
    setDetail(null);
    setActionNote(null);
    setExported(null);
    loadList();
  }, [open, loadList]);

  const openDetail = useCallback((id: string) => {
    setSelectedId(id);
    selectedIdRef.current = id;
    setDetail(null);
    setActionNote(null);
    setExported(null);
    setDetailLoading(true);
    Promise.all([fetchMeetingTranscript(id), fetchMeetingSummaries(id), fetchMeetingNotes(id), fetchMeetingReports(id), fetchFinalSummaryJob(id)])
      .then(([transcript, summaries, notes, reports, finalJob]) => { if (selectedIdRef.current === id) setDetail({ transcript, summaries, notes, reports, finalJob }); })
      .catch((err) => { if (selectedIdRef.current === id) setActionNote(`Could not load meeting: ${(err as Error).message}`); })
      .finally(() => { if (selectedIdRef.current === id) setDetailLoading(false); });
  }, []);

  useEffect(() => {
    if (!open || !selectedId || !detail?.finalJob || !["queued", "running"].includes(detail.finalJob.status)) return;
    let cancelled = false;
    const timer = window.setInterval(async () => {
      try {
        const job = await fetchFinalSummaryJob(selectedId);
        if (cancelled || selectedIdRef.current !== selectedId || !job) return;
        if (job.status === "succeeded") {
          const [summaries, reports] = await Promise.all([fetchMeetingSummaries(selectedId), fetchMeetingReports(selectedId)]);
          if (!cancelled && selectedIdRef.current === selectedId) setDetail((current) => current ? { ...current, finalJob: job, summaries, reports } : current);
        } else {
          setDetail((current) => current ? { ...current, finalJob: job } : current);
        }
      } catch { /* Keep polling; a temporary backend outage is not completion. */ }
    }, 2000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [open, selectedId, detail?.finalJob?.status]);

  const onRetryFinal = useCallback(async (id: string) => {
    setBusy(true);
    setExported(null);
    try {
      const finalJob = await retryFinalSummaryJob(id);
      if (selectedIdRef.current === id) setDetail((current) => current ? { ...current, finalJob } : current);
    } catch (err) {
      if (selectedIdRef.current === id) setActionNote(t("Retry failed: {0}", (err as Error).message));
    } finally { setBusy(false); }
  }, []);

  const onGenerateReport = useCallback(async (id: string) => {
    setBusy(true);
    setExported(null);
    setActionNote(t("Generating report…"));
    try {
      const report = await generateMeetingReport(id);
      if (selectedIdRef.current === id) {
        setDetail((current) => current ? { ...current, reports: [...current.reports, report] } : current);
        setActionNote(t("Report ready"));
      }
    } catch (err) {
      if (selectedIdRef.current === id) setActionNote(t("Report generation failed: {0}", (err as Error).message));
    } finally { setBusy(false); }
  }, []);

  const onExport = useCallback(async (id: string) => {
    setBusy(true);
    setActionNote(null);
    setExported(null);
    try {
      const result = await exportMeetingById(id);
      setExported(result);
      setActionNote(`Exported to ${result.export_dir}`);
    } catch (err) {
      setActionNote(`Export failed: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  }, []);

  const onDelete = useCallback(
    async (id: string) => {
      if (!window.confirm(t("Delete this meeting and all of its transcript, summaries, and notes? This cannot be undone."))) {
        return;
      }
      setBusy(true);
      setActionNote(null);
      setExported(null);
      try {
        await deleteMeeting(id);
        if (selectedId === id) {
          setSelectedId(null);
          selectedIdRef.current = null;
          setDetail(null);
        }
        loadList();
      } catch (err) {
        setActionNote(`Delete failed: ${(err as Error).message}`);
      } finally {
        setBusy(false);
      }
    },
    [selectedId, loadList],
  );

  if (!open) return null;

  const overview = detail ? pickOverview(detail.summaries) : null;
  const latestReport = detail?.reports.reduce<ReportRevision | null>((latest, report) =>
    !latest || (latest.report_id === report.report_id ? report.revision > latest.revision : report.created_at > latest.created_at)
      ? report : latest, null) ?? null;

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-label={t("Meeting history")} onClick={onClose}>
      <div className="modal modal--history" onClick={(e) => e.stopPropagation()}>
        <div className="modal__header">
          <h2>{t("Meeting history")}</h2>
          <button type="button" className="modal__close" onClick={onClose} aria-label={t("Close")}>×</button>
        </div>
        <div className="modal__body history__body">
          <div className="history__list">
            {meetings === null && <p className="history__empty">{t("Loading…")}</p>}
            {meetings !== null && meetings.length === 0 && (
              <p className="history__empty">
                {listError ? t("Could not load history: {0}", listError) : t("No past meetings yet.")}
              </p>
            )}
            {meetings?.map((m) => {
              const duration = formatDuration(m.started_at, m.ended_at);
              const isCurrent = currentMeetingId === m.id;
              return (
                <button
                  key={m.id}
                  type="button"
                  className={`history__item${selectedId === m.id ? " selected" : ""}`}
                  onClick={() => openDetail(m.id)}
                >
                  <span className="history__item-title">{m.title || t("(no transcript yet)")}</span>
                  <span className="history__item-meta">
                    {formatStartedAt(m.started_at)}
                    {" · "}
                    {t(m.segment_count === 1 ? "{0} line" : "{0} lines", m.segment_count)}
                    {duration ? ` · ${duration}` : isCurrent ? t(" · in progress") : ""}
                    {isCurrent ? t(" · current") : ""}
                  </span>
                </button>
              );
            })}
          </div>
          <div className="history__detail">
            {selectedId === null && <p className="history__empty">{t("Select a meeting to view its transcript and summary.")}</p>}
            {selectedId !== null && detailLoading && <p className="history__empty">{t("Loading meeting…")}</p>}
            {selectedId !== null && !detailLoading && detail && (
              <>
                <div className="history__detail-actions">
                  <button type="button" onClick={() => onExport(selectedId)} disabled={busy}>{t("Export")}</button>
                  <button type="button" onClick={() => onGenerateReport(selectedId)} disabled={busy || detail.transcript.length === 0}>{t("Generate report")}</button>
                  <button type="button" className="history__delete" onClick={() => onDelete(selectedId)} disabled={busy}>{t("Delete")}</button>
                </div>
                {detail.finalJob && <p role="status" className="history__note">
                  {t("Final summary: {0}", t(detail.finalJob.status))}
                  {detail.finalJob.error && ` · ${detail.finalJob.error}`}
                  {detail.finalJob.status === "failed" && <button type="button" disabled={busy} onClick={() => onRetryFinal(selectedId)}>{t("Retry")}</button>}
                </p>}
                {latestReport && <ReportView report={latestReport} transcript={detail.transcript} />}
                {overview && (
                  <section className="history__section">
                    <h3>{t("Summary")}</h3>
                    <p className="history__summary">{overview.content}</p>
                  </section>
                )}
                {detail.notes.length > 0 && (
                  <section className="history__section">
                    <h3>{t("Notes")}</h3>
                    <ul className="history__notes">
                      {detail.notes.map((n) => <li key={n.id}>{n.content || `(${n.source_type ?? "note"})`}</li>)}
                    </ul>
                  </section>
                )}
                <section className="history__section">
                  <h3>{t("Transcript")}</h3>
                  {detail.transcript.length === 0 ? (
                    <p className="history__empty">{t("No transcript for this meeting.")}</p>
                  ) : (
                    <div className="history__transcript">
                      {detail.transcript.map((seg) => <p key={seg.id}>{seg.text}</p>)}
                    </div>
                  )}
                </section>
              </>
            )}
          </div>
        </div>
        {actionNote && <div className="modal__footer"><span className="history__note">{t(actionNote)}</span>
          {exported && <ExportLocationActions exported={exported} />}</div>}
      </div>
    </div>
  );
}
