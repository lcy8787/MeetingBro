import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { t, uiLocale, useI18n } from "../i18n";
import { deleteMeeting, exportMeetingById, retryFinalSummaryJob } from "../system/backendApi";
import type { FinalSummaryJob, ReportRevision, SessionState, SummarySnapshot, TranscriptSegment } from "../types";
import { ReportView } from "./ReportView";
import { AudioLevelMeter } from "./AudioLevelMeter";
import { useRecordingHistory } from "./useRecordingHistory";
import { useSummaryGeneration } from "./useSummaryGeneration";
import "../recorder.css";

interface RecorderProps {
  ready: boolean;
  active: boolean;
  state: SessionState | "disconnected";
  meetingId: string | null;
  elapsed: number;
  audioLevel: number;
  segments: TranscriptSegment[];
  summary?: SummarySnapshot;
  report: ReportRevision | null;
  finalJob: FinalSummaryJob | null;
  error: string | null;
  errorCode: string | null;
  feedback: string | null;
  liveTranscript: ReactNode;
  liveExtras: ReactNode;
  summaryOnly?: boolean;
  onStart: () => void;
  onPause: () => void;
  onResume: () => void;
  onStop: () => void;
  onExport: () => void | Promise<void>;
  onSettings: () => void;
  onModelSettings?: () => void;
  onDiagnostics?: () => void;
}

type Tab = "transcript" | "summary";

function clock(seconds: number) {
  const amount = Math.max(0, Math.floor(seconds));
  return [Math.floor(amount / 60), amount % 60].map(n => String(n).padStart(2, "0")).join(":");
}

function dateLabel(iso: string) {
  return new Date(iso).toLocaleString(uiLocale(), {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

function summaryOf(items: SummarySnapshot[]) {
  for (const kind of ["final_summary", "cumulative_meeting_summary", "rolling_summary", "meeting_memory"]) {
    const summary = items.filter(item => item.summary_type === kind && item.generation_method === "llm").at(-1);
    if (summary) return summary;
  }
}

function newestReport(reports: ReportRevision[]) {
  reports = reports.filter(report => report.document.generation_method === "llm");
  const final = reports.filter(report => report.report_type === "meeting");
  return (final.length ? final : reports).reduce<ReportRevision | null>((latest, item) => {
    if (!latest) return item;
    const newer = latest.report_id === item.report_id
      ? item.revision > latest.revision : item.created_at > latest.created_at;
    return newer ? item : latest;
  }, null);
}

function reportText(report: ReportRevision) {
  const { document: doc } = report;
  const blocks = (items: typeof doc.takeaways) => items.flatMap(item => [item.title, item.text, item.detail, ...(item.options ?? []).flatMap(option => [option.label, option.text])]);
  return [doc.title, doc.executive_summary, ...blocks(doc.takeaways),
    ...doc.sections.flatMap(section => [section.title, ...blocks(section.blocks)])].filter(Boolean).join("\n\n");
}

const narrowQuery = "(max-width: 780px)";
function useNarrowWindow() {
  const [narrow, setNarrow] = useState(() => window.matchMedia(narrowQuery).matches);
  useEffect(() => {
    const media = window.matchMedia(narrowQuery);
    const update = () => setNarrow(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return narrow;
}

function Wave({ large = false }: { large?: boolean }) {
  return <span aria-hidden="true" className={large ? "mb-wave mb-wave--large" : "mb-wave"}>
    {[3, 6, 4, 9, 6, 12, 8, 5, 7, 3, 5].map((h, i) => <i key={i} style={{ height: h * (large ? 3 : 1.5) }} />)}
  </span>;
}

function DrawerDialog({ children, close }: { children: ReactNode; close: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current!;
    dialog.showModal();
    return () => dialog.close();
  }, []);
  return <dialog ref={ref} className="mb-overlay-drawer" aria-label={t("Meeting history")}
    onCancel={event => { event.preventDefault(); close(); }}
    onClick={event => { if (event.target === ref.current) close(); }}>
    {children}
  </dialog>;
}

function DeleteRecordingDialog({ title, busy, error, onCancel, onConfirm }: { title: string; busy: boolean; error: string; onCancel: () => void; onConfirm: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const opener = document.activeElement;
    const dialog = ref.current!; dialog.showModal();
    return () => { dialog.close(); if (opener instanceof HTMLElement && opener.isConnected) opener.focus(); };
  }, []);
  return <dialog ref={ref} className="mb-delete-dialog" aria-labelledby="delete-title" aria-describedby="delete-description"
    onCancel={event => { event.preventDefault(); if (!busy) onCancel(); }}>
    <h2 id="delete-title">{t("Delete recording?")}</h2><p className="mb-delete-name">{title}</p>
    <p id="delete-description">{t("Delete this meeting and all of its transcript, summaries, and notes? This cannot be undone.")}</p>
    {error && <p role="alert">{error}</p>}
    <div><button type="button" autoFocus disabled={busy} onClick={onCancel}>{t("Cancel")}</button>
      <button type="button" className="mb-delete-confirm" disabled={busy} onClick={onConfirm}>{busy ? t("Deleting…") : t("Delete")}</button></div>
  </dialog>;
}

type Notice = { text: string; action?: "models" | "diagnostics" };

function RecorderNotice({ notice, close, settings }: { notice: Notice; close: () => void; settings: () => void }) {
  useEffect(() => {
    if (notice.action) return;
    const timer = window.setTimeout(close, 5500);
    return () => window.clearTimeout(timer);
  }, [notice, close]);
  return <div className="mb-notice" role="status"><span>{notice.text}</span>
    {notice.action && <button onClick={settings}>{t("Open Settings")}</button>}
    <button className="mb-close" aria-label={t("Close")} onClick={close}>×</button>
  </div>;
}

export function RecorderView(props: RecorderProps) {
  useI18n();
  const narrow = useNarrowWindow();
  const [desktopDrawer, setDesktopDrawer] = useState(true);
  const [mobileDrawer, setMobileDrawer] = useState(false);
  const drawer = narrow ? mobileDrawer : desktopDrawer;
  const setDrawer = narrow ? setMobileDrawer : setDesktopDrawer;
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [deletedId, setDeletedId] = useState<string | null>(null);
  const currentId = props.meetingId === deletedId ? null : props.meetingId;
  const viewingCurrent = selectedId === null || selectedId === currentId;
  const viewedId = selectedId ?? currentId;
  const isLive = viewingCurrent && props.active;
  const savedId = isLive ? null : viewedId;
  const history = useRecordingHistory(props.ready, drawer, props.active, currentId, savedId, savedId === currentId && !!currentId);
  const { detail } = history;
  const generation = useSummaryGeneration(currentId, props.active && props.state === "running", props.segments, props.report);
  const [tab, setTab] = useState<Tab>("summary");
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busy, setBusy] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const [contextMenu, setContextMenu] = useState<{ id: string; title: string; x: number; y: number } | null>(null);
  const [pendingDelete, setPendingDelete] = useState<{ id: string; title: string } | null>(null);
  useEffect(() => setDeleteError(""), [pendingDelete?.id]);
  const menuRef = useRef<HTMLDivElement>(null);
  const contextOpener = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (!contextMenu) return;
    menuRef.current?.querySelector<HTMLButtonElement>("button")?.focus();
    const dismiss = (event: PointerEvent) => { if (!menuRef.current?.contains(event.target as Node)) setContextMenu(null); };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") { setContextMenu(null); contextOpener.current?.focus(); } };
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", escape);
    const close = () => setContextMenu(null);
    window.addEventListener("resize", close);
    document.addEventListener("scroll", close, true);
    return () => {
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("keydown", escape);
      window.removeEventListener("resize", close);
      document.removeEventListener("scroll", close, true);
    };
  }, [contextMenu]);
  const shareMenu = useRef<HTMLDetailsElement>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const wasRecording = useRef(props.active);

  useEffect(() => {
    if (props.active && !wasRecording.current) {
      setSelectedId(null);
      setTab(props.summaryOnly ? "summary" : "transcript");
      setMobileDrawer(false);
    }
    wasRecording.current = props.active;
  }, [props.active, props.summaryOnly]);

  useEffect(() => {
    if (props.feedback) setNotice({ text: t(props.feedback) });
  }, [props.feedback]);
  useEffect(() => {
    if (!props.error) return;
    const modelError = props.errorCode === "llm_unavailable" || props.errorCode === "translation_unavailable";
    setNotice({ text: t(props.error), action: modelError ? "models" : "diagnostics" });
  }, [props.error, props.errorCode]);
  const closeNotice = useCallback(() => setNotice(null), []);

  useEffect(() => {
    if (scroll.current) scroll.current.scrollTop = 0;
    if (shareMenu.current) shareMenu.current.open = false;
  }, [tab, viewedId]);

  useEffect(() => {
    const dismiss = (event: PointerEvent) => {
      if (shareMenu.current && !shareMenu.current.contains(event.target as Node)) shareMenu.current.open = false;
    };
    const onEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && shareMenu.current?.open) {
        shareMenu.current.open = false;
        shareMenu.current.querySelector("summary")?.focus();
      }
    };
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", onEscape);
    return () => {
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("keydown", onEscape);
    };
  }, []);

  const item = history.list?.find(row => row.id === viewedId);
  const previousJob = savedId ? detail?.job : props.finalJob;
  const generatedReport = viewedId ? generation.reports[viewedId] : null;
  const report = newestReport([
    ...(savedId ? detail?.reports ?? [] : props.report ? [props.report] : []),
    ...(generatedReport ? [generatedReport] : []),
  ]);
  const job = previousJob?.status === "failed" && report && report.created_at > previousJob.updated_at ? null : previousJob;
  const summary = savedId ? summaryOf(detail?.summaries ?? []) : props.summary?.generation_method === "llm" ? props.summary : undefined;
  const transcript = savedId ? detail?.transcript ?? [] : props.segments;
  const loading = !!savedId && !detail && !history.detailError;
  const blank = !viewedId && !props.active;
  const readyToCopy = (tab === "transcript" && transcript.length > 0) || (tab === "summary" && !!(summary || report));
  const generateSummary = async () => {
    if (!viewedId) return;
    const result = await generation.generate(viewedId);
    if (result && savedId) history.refreshDetail();
  };
  const run = async (action: () => Promise<void>) => {
    if (shareMenu.current) shareMenu.current.open = false;
    setBusy(true);
    try { await action(); }
    catch (e) { setNotice({ text: t("Could not finish the action: {0}", (e as Error).message) }); }
    finally { setBusy(false); }
  };
  const copy = () => run(async () => {
    let text = summary?.content ?? "";
    if (tab === "transcript") text = transcript.map(segment => segment.text).join("\n");
    else if (report) text = reportText(report);
    await navigator.clipboard.writeText(text);
    setNotice({ text: t("Copied") });
  });
  const exportRecording = () => run(async () => {
    if (viewingCurrent && currentId) { await props.onExport(); return; }
    if (savedId) {
      const result = await exportMeetingById(savedId);
      setNotice({ text: t("Export ready in {0}", result.export_dir) });
    }
  });
  const remove = () => {
    if (!pendingDelete || busy) return;
    const id = pendingDelete.id;
    void run(async () => {
      setDeleteError("");
      try { await deleteMeeting(id); }
      catch (error) { setDeleteError(t("Could not finish the action: {0}", (error as Error).message)); throw error; }
      if (id === currentId) setDeletedId(id);
      setSelectedId(current => current === id ? null : current);
      setPendingDelete(null);
      history.refreshList();
      setNotice({ text: t("Recording deleted") });
    });
  };
  const select = (id: string | null) => {
    setSelectedId(id);
    setMobileDrawer(false);
  };
  const settings = () => { setMobileDrawer(false); props.onSettings(); };
  const start = () => { setMobileDrawer(false); props.onStart(); };
  const transport = <div className="mb-transport">
    <button type="button" onClick={props.state === "paused" ? props.onResume : props.onPause}
      disabled={props.state !== "running" && props.state !== "paused"}>
      {props.state === "paused" ? t("Resume") : t("Pause")}
    </button>
    <button type="button" onClick={props.onStop}>{t("Stop")}</button>
  </div>;
  const sessionTitle = props.state === "paused" ? t("Paused") : t("Recording");
  const sidebar = <aside className="mb-sidebar" aria-label={t("Meeting history")}>
    <div className="mb-brand">
      <div className="mb-wordmark"><Wave /><span>MeetingBro</span>
        <button type="button" className="mb-collapse" aria-label={t("Hide history")} title={t("Hide history")} onClick={() => setDrawer(false)}>‹</button>
      </div>
      <h1>{t("AI recorder")}</h1>
      {props.active ? <><div className="mb-record-status">{sessionTitle} · {clock(props.elapsed)} <AudioLevelMeter level={props.audioLevel} running={props.state === "running"} /></div>{transport}</>
        : <button type="button" className="mb-start" onClick={start} disabled={!props.ready}>
          <span className="mb-mic" aria-hidden="true" />{t("Start recording")}
        </button>}
    </div>
    <div className="mb-history-title">{t("Recording history")}</div>
    {props.active && <button className="mb-history-item mb-current" aria-current={viewingCurrent ? "true" : undefined}
      onClick={() => select(null)}><strong>{t("Current recording")}</strong><small><i />{sessionTitle} · {clock(props.elapsed)}</small></button>}
    <div className="mb-history-list">
      {history.listError && <p>{t("Could not load history: {0}", history.listError)} <button onClick={history.refreshList}>{t("Retry")}</button></p>}
      {!history.list && !history.listError && <p>{t("Loading…")}</p>}
      {history.list?.length === 0 && <p>{t("No past meetings yet.")}</p>}
      {history.list?.filter(row => !(props.active && row.id === currentId)).map(row => <button key={row.id}
        type="button" className="mb-history-item" aria-current={viewedId === row.id ? "true" : undefined}
        onContextMenu={event => {
          event.preventDefault(); contextOpener.current = event.currentTarget;
          const rect = event.currentTarget.getBoundingClientRect();
          setContextMenu({ id: row.id, title: row.title || t("Untitled recording"),
            x: Math.max(8, Math.min(event.clientX || rect.left + 20, window.innerWidth - 190)),
            y: Math.max(8, Math.min(event.clientY || rect.bottom, window.innerHeight - 64)) });
        }}
        onClick={() => select(row.id)}><strong>{row.title || t("Untitled recording")}</strong>
        <small>{dateLabel(row.started_at)}</small></button>)}
    </div>
    {contextMenu && <div className="mb-context-menu" ref={menuRef} role="menu" aria-label={t("Recording actions")}
      style={{ left: contextMenu.x, top: contextMenu.y }}>
      <button type="button" role="menuitem" disabled={busy} onClick={() => { setPendingDelete(contextMenu); setContextMenu(null); }}>{t("Delete")}</button>
    </div>}
  </aside>;

  return <div className="recorder-shell">
    {drawer && (narrow ? <DrawerDialog close={() => setMobileDrawer(false)}>{sidebar}</DrawerDialog> : sidebar)}
    <main className="mb-reader">
      <header className="mb-toolbar">
        {!drawer && <button type="button" className="mb-icon-button" aria-label={t("Show history")}
          title={t("Show history")} onClick={() => setDrawer(true)}><span className="mb-drawer-icon" aria-hidden="true" /></button>}
        {props.active && (!drawer || !viewingCurrent) && <button type="button" className="mb-recording-link"
          onClick={() => { select(null); if (viewingCurrent) setDrawer(true); }}><i />{viewingCurrent ? sessionTitle : t("Current recording")} · {clock(props.elapsed)}</button>}
        <div className="mb-read-actions">
          <button type="button" className="mb-pill" onClick={() => setTab(tab === "summary" ? "transcript" : "summary")}
            aria-label={tab === "summary" ? t("View transcript") : t("View summary")}>
            <span aria-hidden="true" className="mb-page-icon" />{tab === "summary" ? t("Original text") : t("Summary")}
          </button>
          <details className="mb-share" ref={shareMenu}>
            <summary className="mb-pill"><span aria-hidden="true" className="mb-share-icon">↗</span>{t("Share")}</summary>
            <div className="mb-menu">
              <button type="button" onClick={() => void copy()} disabled={busy || !readyToCopy}>{t("Copy")}</button>
              <button type="button" onClick={() => void exportRecording()} disabled={busy || !viewedId}>{t("Export")}</button>
              {savedId && <button type="button" className="mb-delete" disabled={busy || generation.busy} onClick={() => { if (shareMenu.current) shareMenu.current.open = false; setPendingDelete({ id: savedId, title: item?.title || t("Untitled recording") }); }}>{t("Delete")}</button>}
            </div>
          </details>
          <button type="button" className="mb-pill mb-settings" onClick={settings}
            aria-label={t("Settings")} title={t("Settings")}><span aria-hidden="true">⚙</span><span>{t("Settings")}</span></button>
        </div>
      </header>
      {isLive && <div className="mb-input-status"><AudioLevelMeter level={props.audioLevel} running={props.state === "running"} /><span>{props.state === "paused" ? t("Paused") : t("Input level")}</span></div>}
      <div className="mb-scroll" ref={scroll}>
        {tab === "summary" && <div className="mb-summary-controls">
          <div className="mb-summary-controls__actions">
            <button type="button" className="mb-primary" disabled={busy || generation.busy || loading || !viewedId || !transcript.some(s => !!s.text.trim())}
              aria-busy={generation.busy} onClick={() => void generateSummary()}>{t(generation.busy ? "Generating summary…" : "Generate summary")}</button>
            <label className="mb-summary-auto"><input type="checkbox" role="switch" checked={generation.automatic}
              onChange={event => generation.setAutomatic(event.target.checked)} />{t("Automatically generate summaries")}</label>
          </div>
          <p>{generation.automatic ? t("During recording, check every 5 minutes for the first summary, then update every 10 minutes when the transcript changes. Model API charges may apply.")
            : t("Automatic updates are off. Use Generate summary for an immediate summary.")}</p>
          <p>{t("Stopping a recording automatically generates one final summary after the transcript is saved, even when automatic updates are off. Model API charges may apply.")}</p>
          {generation.busy && <p role="status">{t("The current model request will finish even if automatic summaries are turned off.")}</p>}
          {generation.error && <p role="alert" className="mb-summary-error">{t(generation.error)} <button type="button" onClick={props.onModelSettings ?? props.onSettings}>{t("Open Settings")}</button></p>}
        </div>}
        {blank && <div className="mb-empty">
          <div className="mb-empty-mark"><Wave large /></div>
          <h2>{t("Start a recording")}</h2>
          <p>{t("Your transcript and summary will appear here.")}</p>
          <button className="mb-primary" type="button" onClick={start} disabled={!props.ready}>{t("Start recording")}</button>
        </div>}
        {!blank && <div className="mb-document">
          {tab === "transcript" && <header className="mb-doc-heading"><small>{t("Original text")}</small>
            <h2>{item?.title || (isLive ? t("Current recording") : t("Transcript"))}</h2>
            {item && <time>{dateLabel(item.started_at)}</time>}
          </header>}
          {loading && <p role="status" className="mb-status">{t("Loading meeting…")}</p>}
          {history.detailError && savedId && <p className="mb-status" role="alert">{t("Could not load meeting: {0}", history.detailError)} <button onClick={history.refreshDetail}>{t("Retry")}</button></p>}
          {tab === "transcript" && (isLive ? props.liveTranscript : detail && <section className="mb-transcript">
            {transcript.length === 0 && <p className="mb-status">{t("No transcript for this meeting.")}</p>}
            {transcript.map(segment => <article key={segment.id}><time>{clock(segment.start_time)}</time>
              <div>{segment.text}{Object.entries(segment.translations).filter(([, text]) => text && text !== segment.text).map(([lang, text]) =>
                <p className="mb-translation" lang={lang} key={lang}>{text}</p>)}</div>
            </article>)}
          </section>)}
          {!loading && tab === "summary" && <>
            {job && <p className="mb-job" role="status">{t("Final summary: {0}", t(job.status))}
              {job.error && ` · ${job.error}`}{job.status === "failed" && savedId && <button disabled={busy} onClick={() => void run(async () => {
                await retryFinalSummaryJob(savedId); history.refreshDetail();
              })}>{t("Retry")}</button>}
            </p>}
            {report && <ReportView key={`${report.meeting_id}-${report.report_id}`} report={report} transcript={transcript} />}
            {!report && summary && <section className="mb-summary"><h2>{item?.title || t("Summary")}</h2><p>{summary.content}</p></section>}
            {!report && !summary && !history.detailError && <p className="mb-status">{t("No model-generated summary yet. Use Generate summary or enable automatic summaries.")}</p>}

          </>}
        </div>}
      </div>
    </main>
    {pendingDelete && <DeleteRecordingDialog title={pendingDelete.title} busy={busy} error={deleteError} onCancel={() => setPendingDelete(null)} onConfirm={remove} />}
    {notice && <RecorderNotice notice={notice} close={closeNotice} settings={() => {
      const action = notice.action === "models" ? props.onModelSettings : props.onDiagnostics;
      closeNotice(); setMobileDrawer(false); (action ?? props.onSettings)();
    }} />}
  </div>;
}
