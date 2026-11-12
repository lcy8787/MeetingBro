import { useEffect, useState } from "react";
import { t } from "../i18n";
import type { ExportMeetingResponse } from "../types";
import { ExportLocationActions } from "./ExportLocationActions";

export type NoticeType = "info" | "success" | "warning" | "error";
export type NoticeFeedback = { text: string; type: NoticeType; exported?: ExportMeetingResponse };
export type Notice = NoticeFeedback & { action?: "models" | "diagnostics" };

function NoticeIcon({ type }: { type: NoticeType }) {
  return <svg className="mb-notice__icon" viewBox="0 0 24 24" aria-hidden="true">
    {type === "warning" ? <path fill="currentColor" d="M10.3 3.8a2 2 0 0 1 3.4 0l8 14A2 2 0 0 1 20 21H4a2 2 0 0 1-1.7-3.2z" />
      : <circle cx="12" cy="12" r="10" fill="currentColor" />}
    <g fill="none" stroke="white" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      {type === "success" ? <path d="m7.5 12 3 3 6-6" />
        : type === "error" ? <path d="m8.5 8.5 7 7m0-7-7 7" />
          : type === "info" ? <><path d="M12 11v6" /><path d="M12 7h.01" /></>
            : <><path d="M12 9v5" /><path d="M12 17h.01" /></>}
    </g>
  </svg>;
}

export function RecorderNotice({ notice, close, settings }: { notice: Notice; close: () => void; settings: () => void }) {
  const [leaving, setLeaving] = useState(false);
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  useEffect(() => { setLeaving(false); }, [notice]);
  useEffect(() => {
    // Warnings, errors and actionable notices stay available until dismissed.
    if (notice.action || notice.exported || notice.type === "warning" || notice.type === "error" || hovered || focused || leaving) return;
    const timer = window.setTimeout(() => setLeaving(true), 5500);
    return () => window.clearTimeout(timer);
  }, [notice, hovered, focused, leaving]);
  useEffect(() => {
    if (!leaving) return;
    const timer = window.setTimeout(close, 200);
    return () => window.clearTimeout(timer);
  }, [leaving, notice, close]);

  return <div className={`mb-notice mb-notice--${notice.type}${leaving ? " mb-notice--leaving" : ""}`}
    role={notice.type === "error" ? "alert" : "status"} aria-atomic="true"
    onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}
    onFocusCapture={() => setFocused(true)}
    onBlurCapture={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setFocused(false); }}>
    <NoticeIcon type={notice.type} />
    <div className="mb-notice__body">
      <span className="mb-notice__text">{notice.text}</span>
      {notice.exported && <ExportLocationActions exported={notice.exported} />}
      {notice.action && <button type="button" className="mb-notice__action" onClick={settings}>{t("Open Settings")}</button>}
    </div>
    <button type="button" className="mb-close" aria-label={t("Close")} onClick={() => setLeaving(true)}>
      <svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 4 8 8m0-8-8 8" /></svg>
    </button>
  </div>;
}
