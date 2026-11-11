import { useCallback, useEffect, useState } from "react";
import {
  fetchMeetings, fetchMeetingTranscript, fetchMeetingSummaries, fetchMeetingNotes,
  fetchMeetingReports, fetchFinalSummaryJob, type MeetingListItem,
} from "../system/backendApi";
import type { FinalSummaryJob, Note, ReportRevision, SummarySnapshot, TranscriptSegment } from "../types";

interface RecordingDetail {
  id: string;
  transcript: TranscriptSegment[];
  summaries: SummarySnapshot[];
  notes: Note[];
  reports: ReportRevision[];
  job: FinalSummaryJob | null;
}

async function readRecording(id: string): Promise<RecordingDetail> {
  const [transcript, summaries, notes, reports, job] = await Promise.all([
    fetchMeetingTranscript(id), fetchMeetingSummaries(id), fetchMeetingNotes(id),
    fetchMeetingReports(id), fetchFinalSummaryJob(id),
  ]);
  return { id, transcript, summaries, notes, reports, job };
}

export function useRecordingHistory(ready: boolean, open: boolean, active: boolean, currentId: string | null, selectedId: string | null, waitingForFinal: boolean) {
  const [list, setList] = useState<MeetingListItem[] | null>(null);
  const [listError, setListError] = useState("");
  const [detail, setDetail] = useState<RecordingDetail | null>(null);
  const [detailError, setDetailError] = useState<{ id: string; text: string } | null>(null);
  const [listVersion, setListVersion] = useState(0);
  const [detailVersion, setDetailVersion] = useState(0);
  const refreshList = useCallback(() => setListVersion(v => v + 1), []);
  const refreshDetail = useCallback(() => setDetailVersion(v => v + 1), []);

  useEffect(() => {
    if (!ready || !open) return;
    let cancelled = false;
    setListError("");
    fetchMeetings().then(rows => {
      if (!cancelled) setList(rows);
    }).catch((e: Error) => {
      if (!cancelled) setListError(e.message);
    });
    return () => { cancelled = true; };
  }, [ready, open, currentId, active, listVersion]);

  useEffect(() => {
    if (!ready || !selectedId) return;
    let cancelled = false;
    setDetail(null);
    setDetailError(null);
    readRecording(selectedId).then(recording => {
      if (!cancelled) setDetail(recording);
    }).catch((e: Error) => {
      if (!cancelled) setDetailError({ id: selectedId, text: e.message });
    });
    return () => { cancelled = true; };
  }, [ready, selectedId, detailVersion]);

  const visible = detail?.id === selectedId ? detail : null;
  const status = visible?.job?.status;
  const hasDetail = !!visible;
  useEffect(() => {
    // The just-stopped session may still be flushing its final transcript.
    const pending = status === "queued" || status === "running" || (!status && waitingForFinal);
    if (!selectedId || !hasDetail || !pending) return;
    let cancelled = false;
    let timer: number;
    let absentJobAttempts = 0;
    const poll = async () => {
      try {
        if (!status) {
          // Read the transcript after checking ended_at: a disabled final job still
          // has a final flush, and the list marks the recording ended after that flush.
          absentJobAttempts++;
          const rows = await fetchMeetings();
          if (cancelled) return;
          const updated = await readRecording(selectedId);
          if (cancelled) return;
          setList(rows);
          setDetail(updated);
          const ended = rows.find(row => row.id === selectedId)?.ended_at;
          if (!ended && !updated.job && absentJobAttempts < 60) timer = window.setTimeout(poll, 2000);
          return;
        }
        const job = await fetchFinalSummaryJob(selectedId);
        if (cancelled) return;
        if (job?.status === "succeeded") {
          const updated = await readRecording(selectedId);
          if (!cancelled) { setDetail(updated); refreshList(); }
          return;
        }
        if (job) setDetail(current => current?.id === selectedId ? { ...current, job } : current);
        if (job?.status === "failed") return;
      } catch { /* Keep polling a pending job after a temporary disconnect. */ }
      if (!cancelled && (status || absentJobAttempts < 60)) timer = window.setTimeout(poll, 2000);
    };
    timer = window.setTimeout(poll, 2000);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [selectedId, hasDetail, status, waitingForFinal, refreshList]);

  return { list, listError, detail: visible, detailError: detailError?.id === selectedId ? detailError.text : "", refreshList, refreshDetail };
}
