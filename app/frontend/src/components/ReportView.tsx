import { useRef, useState } from "react";
import { t } from "../i18n";
import type { ReportRevision, TranscriptSegment } from "../types";

import { blockText, Evidence, ReportBlockView as Block } from "./reportComponents";
import "../reportEditorial.css";

interface Props {
  report: ReportRevision;
  transcript: TranscriptSegment[];
}

export function ReportView({ report, transcript }: Props) {
  const { document: doc } = report;
  const root = useRef<HTMLDivElement>(null);
  const sectionRefs = useRef<Array<HTMLElement | null>>([]);
  const [message, setMessage] = useState("");
  const sources = new Map(transcript.map((segment) => [segment.id, segment]));
  const copy = async (text: string) => {
    try { await navigator.clipboard.writeText(text); setMessage(t("Copied")); }
    catch { setMessage(t("Copy failed")); }
  };
  const sectionText = (index: number) => {
    const section = doc.sections[index];
    return [section.title, ...section.blocks.map(blockText)].join("\n\n");
  };
  if (doc.generation_method !== "llm") return null;
  const scrollToSection = (index: number) => sectionRefs.current[index]?.scrollIntoView({ behavior: "smooth", block: "start" });
  return <div className="report-view" ref={root}>
    <header className="report-hero">
      <div className="report-eyebrow">{t("MEETING REPORT")} · {t("Revision {0}", report.revision)}</div>
      <h2>{doc.title}</h2>
      {doc.executive_summary && <><p className="report-hero__summary">{doc.executive_summary}</p><Evidence ids={doc.executive_summary_source_segment_ids} sources={sources} /></>}
      <div className="report-meta">{t("Generated from transcript")} · {new Date(report.created_at).toLocaleString()}</div>
      {doc.warnings.map((warning, i) => <p className="report-warning" role="status" key={i}>{t(warning)}</p>)}
      <div className="report-controls">
        <button type="button" onClick={() => void copy([doc.title, doc.executive_summary, ...doc.takeaways.map(blockText), ...doc.sections.map((_, i) => sectionText(i))].filter(Boolean).join("\n\n"))}>{t("Copy report")}</button>
        <span role="status" aria-live="polite">{message}</span>
      </div>
      {doc.takeaways.length > 0 && <div className="report-grid">{doc.takeaways.map((block, i) => <Block key={`${report.revision}-takeaway-${i}`} block={block} sources={sources} />)}</div>}
    </header>
    <nav className="report-nav" aria-label={t("Report contents")}>
      {doc.sections.map((section, i) => <button key={i} type="button" onClick={() => scrollToSection(i)}>{String(i + 1).padStart(2, "0")} · {section.title}</button>)}
    </nav>
    {doc.sections.map((section, i) => <section key={section.id || i} ref={(node) => { sectionRefs.current[i] = node; }} className={`report-section report-section--${section.layout_type}`}>
      <div className="report-eyebrow">PART {String(i + 1).padStart(2, "0")}</div>
      <div className="report-section__heading"><h3>{section.title}</h3><button type="button" onClick={() => void copy(sectionText(i))}>{t("Copy section")}</button></div>
      <details open><summary>{t("Show or hide section")}</summary>
        <div className={`report-grid report-grid--${section.layout_type}`}>{section.blocks.map((block, j) => <Block key={`${report.revision}-${j}`} block={block} sources={sources} />)}</div>
      </details>
    </section>)}
    <button className="report-top" type="button" onClick={() => root.current?.scrollIntoView({ behavior: "smooth" })}>{t("Back to top")}</button>
  </div>;
}
