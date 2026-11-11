import { t, useI18n } from "../i18n";
import type { BackendState } from "../meetingbro-bridge";

interface BackendGateProps {
  state: BackendState;
  detail: string | null;
  logTail: string[];
  onRetry: () => void;
  onOpenLog: () => void;
}

const TITLES: Partial<Record<BackendState, string>> = {
  locating: "Starting MeetingBro…",
  starting: "Starting MeetingBro…",
  "downloading-model": "Preparing the speech model",
  failed: "Couldn't start MeetingBro",
  stopped: "Backend stopped",
};

const SUBTITLES: Partial<Record<BackendState, string>> = {
  "downloading-model": "Loading installed speech models. Model downloads are a separate setup step.",
};

// Full-screen overlay shown until the backend is ready, so a non-technical user
// never faces a silent dead connection. Returns null when ready (App renders).
export function BackendGate({ state, detail, logTail, onRetry, onOpenLog }: BackendGateProps) {
  useI18n();
  const isError = state === "failed" || state === "stopped";
  const isWorking = state === "locating" || state === "starting" || state === "downloading-model";
  const title = TITLES[state] ?? t("Connecting…");
  const subtitle = SUBTITLES[state];

  return (
    <div className="backend-gate" role="alertdialog" aria-busy={isWorking} aria-label={title}>
      <div className="backend-gate__card">
        {isWorking && <div className="backend-gate__spinner" aria-hidden="true" />}
        {isError && <div className="backend-gate__icon" aria-hidden="true">⚠️</div>}
        <h1 className="backend-gate__title">{t(title)}</h1>
        {subtitle && <p className="backend-gate__subtitle">{t(subtitle)}</p>}
        {detail && <p className="backend-gate__detail">{t(detail)}</p>}

        {isError && (
          <div className="backend-gate__actions">
            <button type="button" className="backend-gate__btn backend-gate__btn--primary" onClick={onRetry}>
              {t("Retry")}</button>
            <button type="button" className="backend-gate__btn" onClick={onOpenLog}>
              {t("View log")}</button>
          </div>
        )}

        {isError && logTail.length > 0 && (
          <pre className="backend-gate__log">{logTail.slice(-12).join("\n")}</pre>
        )}
      </div>
    </div>
  );
}
