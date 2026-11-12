import { t } from "../i18n";
import { SummaryGenerationError, summaryFailureMessage } from "../system/backendApi";

export function SummaryFailureNotice({ error, retry, settings, disabled = false }: {
  error: SummaryGenerationError;
  retry: () => void;
  settings: () => void;
  disabled?: boolean;
}) {
  return <p role="alert" className="mb-summary-error">
    {summaryFailureMessage(error, t)} <button type="button" disabled={disabled} onClick={retry}>{t("Retry")}</button>
    {error.needsSettings && <button type="button" onClick={settings}>{t("Open Settings")}</button>}
  </p>;
}
