import { useEffect, useState } from "react";
import { t } from "../i18n";
import type { ExportMeetingResponse } from "../types";

// Action controls shared by the existing recorder notice and history footer.
export function ExportLocationActions({ exported }: { exported: ExportMeetingResponse }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => { setMessage(""); }, [exported]);
  const bridge = window.meetingbro;
  const canReveal = !!(exported.locationId && bridge?.revealExport);
  const canCopy = bridge?.exportMeeting
    ? !!(exported.locationId && bridge.copyExportPath)
    : !!navigator.clipboard?.writeText;

  const act = async (kind: "reveal" | "copy") => {
    setBusy(true);
    setMessage("");
    try {
      if (kind === "reveal") {
        if (!exported.locationId || !(await bridge?.revealExport?.(exported.locationId))?.ok) throw new Error();
      } else if (bridge?.exportMeeting) {
        if (!exported.locationId || !(await bridge.copyExportPath?.(exported.locationId))?.ok) throw new Error();
        setMessage("Path copied");
      } else {
        await navigator.clipboard.writeText(exported.export_dir);
        setMessage("Path copied");
      }
    } catch {
      setMessage(kind === "reveal" ? "Could not open the export location. The files may have moved or been deleted." : "Could not copy the export path.");
    } finally { setBusy(false); }
  };

  return <div className="export-location-actions">
    <button type="button" className="mb-notice__action" disabled={busy || !canReveal}
      title={!canReveal ? t("Opening the export location requires a valid desktop export.") : undefined}
      onClick={() => void act("reveal")}>{t("Open file location")}</button>
    <button type="button" className="mb-notice__action" disabled={busy || !canCopy}
      onClick={() => void act("copy")}>{t("Copy path")}</button>
    {message && <span className="export-location-actions__status" role="status">{t(message)}</span>}
    {!canReveal && <span className="export-location-actions__status">{t("Opening the export location requires a valid desktop export.")}</span>}
  </div>;
}
