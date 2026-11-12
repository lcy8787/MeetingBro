import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { generateMeetingReport, SummaryGenerationError } from "../system/backendApi";
import type { ReportRevision, TranscriptSegment } from "../types";

export const FIRST_SUMMARY_INTERVAL_MS = 5 * 60_000;
export const AUTO_SUMMARY_INTERVAL_MS = 10 * 60_000;

export function useSummaryGeneration(meetingId: string | null, running: boolean, segments: TranscriptSegment[], existingReport: ReportRevision | null) {
  // Opt in separately for each app launch, so opening a recording never starts
  // paid requests because of an old preference.
  const [automatic, setAutomatic] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<SummaryGenerationError | null>(null);
  const [errorMeetingId, setErrorMeetingId] = useState<string | null>(null);
  const [reports, setReports] = useState<Record<string, ReportRevision>>({});
  const generated = meetingId ? reports[meetingId] ?? null : null;
  const known = existingReport?.meeting_id === meetingId && existingReport.document.generation_method === "llm" ? existingReport : null;
  const lastReport = generated && (!known || generated.created_at >= known.created_at) ? generated : known;
  const interval = lastReport ? AUTO_SUMMARY_INTERVAL_MS : FIRST_SUMMARY_INTERVAL_MS;
  const lastGeneratedAt = lastReport?.created_at;
  const inFlight = useRef(false);
  const processed = useRef(new Map<string, string>());
  const fingerprint = useMemo(() => JSON.stringify(segments.map(s => [s.id, s.revision, s.text])), [segments]);
  const latest = useRef({ meetingId, fingerprint, hasText: false });
  latest.current = { meetingId, fingerprint, hasText: segments.some(s => !!s.text.trim()) };

  const generate = useCallback(async (id: string) => {
    if (inFlight.current) return;
    inFlight.current = true;
    const source = latest.current;
    setBusy(true);
    setError(null);
    setErrorMeetingId(id);
    try {
      const report = await generateMeetingReport(id);
      if (report.meeting_id !== id || report.document.generation_method !== "llm") throw new Error("The model did not produce a summary.");
      setReports(current => ({ ...current, [id]: report }));
      if (source.meetingId === id) processed.current.set(id, source.fingerprint);
      return report;
    } catch (reason) {
      setError(reason instanceof SummaryGenerationError ? reason : new SummaryGenerationError());
      // A failed paid request needs an explicit retry. Do not keep charging
      // for retries while the model is unavailable or returns invalid data.
      setAutomatic(false);
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    if (!automatic || !running || !meetingId) return;
    const timer = window.setInterval(() => {
      const source = latest.current;
      if (source.meetingId === meetingId && source.hasText && !inFlight.current
          && processed.current.get(meetingId) !== source.fingerprint) {
        void generate(meetingId);
      }
    }, interval);
    return () => window.clearInterval(timer);
  }, [automatic, running, meetingId, interval, lastGeneratedAt, generate]);

  return { automatic, setAutomatic, busy, error, errorMeetingId, reports, generated, generate, interval };
}
