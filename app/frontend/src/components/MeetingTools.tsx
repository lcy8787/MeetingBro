import { useMemo, useState } from "react";
import { t, uiLocale } from "../i18n";
import type { Note, SummarySnapshot } from "../types";

const summaryLabels = {
  rolling_summary: "Summary updates",
  refined_transcript: "Clean Notes",
  cumulative_meeting_summary: "Meeting Board",
} as const;
export type RefreshableSummary = keyof typeof summaryLabels;

interface Props {
  snapshots: SummarySnapshot[];
  notes: Note[];
  onRefresh?: (type: RefreshableSummary) => void;
  onSave?: (item: SummarySnapshot) => Promise<void>;
  refreshing?: RefreshableSummary | null;
  onFeedback?: (message: string) => void;
}

function rows(snapshot?: SummarySnapshot): Record<string, unknown>[] {
  if (!snapshot) return [];
  try {
    const parsed: unknown = JSON.parse(snapshot.content);
    if (Array.isArray(parsed)) return parsed.filter(item => item && typeof item === "object");
  } catch { /* Older snapshots may contain plain text. */ }
  return [];
}

function stamp(seconds: number) {
  return [Math.floor(seconds / 60), Math.floor(seconds % 60)].map(n => String(n).padStart(2, "0")).join(":");
}

export function MeetingTools({ snapshots, notes, onRefresh, onSave, refreshing, onFeedback }: Props) {
  const [tab, setTab] = useState<RefreshableSummary | "notes">("notes");
  const ordered = useMemo(() => snapshots.filter(item => item.generation_method === "llm").sort((a, b) => a.created_at.localeCompare(b.created_at)), [snapshots]);
  const history = ordered.filter(s => s.summary_type === tab || (tab === "rolling_summary" && s.summary_type === "final_summary"));
  const latest = history.at(-1);
  const chapters = rows(ordered.filter(s => s.summary_type === "chapter_list").at(-1));
  const actions = rows(ordered.filter(s => s.summary_type === "action_item_list").at(-1));
  const copy = async () => {
    if (!latest) return;
    try {
      await navigator.clipboard.writeText(latest.content);
      onFeedback?.(t("Copied"));
    } catch { onFeedback?.(t("Copy failed")); }
  };

  return <details className="meeting-tools">
    <summary>{t("Notes and summary tools")}</summary>
    <div className="meeting-tools-body">
      <label className="meeting-tools-picker">
        <span>{t("View")}</span>
        <select value={tab} onChange={e => setTab(e.target.value as typeof tab)}>
          <option value="notes">{t("Notes")}</option>
          {Object.entries(summaryLabels).map(([key, label]) => <option key={key} value={key}>{t(label)}</option>)}
        </select>
      </label>
      {tab === "notes" ? <>
        {chapters.length > 0 && <section><h3>{t("Chapters")}</h3><ol>{chapters.map((item, i) =>
          <li key={i}><small>{stamp(Number(item.time_start ?? 0))} – {stamp(Number(item.time_end ?? 0))}</small>
            <strong>{String(item.title ?? t("Untitled chapter"))}</strong><p>{String(item.summary ?? "")}</p></li>)}</ol></section>}
        {actions.length > 0 && <section><h3>{t("Action Items")}</h3><ul>{actions.map((item, i) =>
          <li key={i}>{String(item.text ?? "")}<small>{[item.assignee, item.due].filter(Boolean).join(" · ")}</small></li>)}</ul></section>}
        {notes.length > 0 ? <ul>{notes.map(note => <li key={note.id}>
          <small>{note.source_type === "bookmark" ? t("Bookmark") : t(note.source_type ?? "Notes")} · {note.time_seconds != null ? stamp(note.time_seconds) : new Date(note.created_at).toLocaleTimeString(uiLocale())}</small>
          <p>{note.content || t("Important moment")}</p></li>)}</ul> :
          <p className="tools-empty">{t("No saved notes yet.")}</p>}
      </> : <>
        {latest ? <article><small>{stamp(latest.time_start)} – {stamp(latest.time_end)}</small><p>{latest.content}</p></article> :
          <p className="tools-empty">{t("No snapshots yet.")}</p>}
        <div className="meeting-tools-actions">
          {onRefresh && <button type="button" onClick={() => onRefresh(tab)} disabled={!!refreshing}>{t(refreshing === tab ? "Refreshing…" : "Refresh")}</button>}
          {latest && <button type="button" onClick={copy}>{t("Copy Latest")}</button>}
          {latest && onSave && <button type="button" onClick={() => void onSave(latest)}>{t("Save Latest")}</button>}
        </div>
        {history.length > 1 && <details><summary>{t("Previous updates")} ({history.length - 1})</summary>
          {history.slice(0, -1).reverse().map(item => <article key={item.id}>
            <small>{stamp(item.time_start)} – {stamp(item.time_end)} · {new Date(item.created_at).toLocaleTimeString(uiLocale())}</small><p>{item.content}</p>
          </article>)}</details>}
      </>}
    </div>
  </details>;
}
