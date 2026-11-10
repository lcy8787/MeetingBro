import { useRef, useState } from "react";
const t = (text: string, ...values: Array<string | number>) => text.replace(/\{(\d+)\}/g, (_, i) => String(values[Number(i)] ?? ""));
import type { ReportBlock, ReportRevision, TranscriptSegment } from "../types";

interface Props {
  report: ReportRevision;
  transcript: TranscriptSegment[];
}

function Block({ block, sources }: { block: ReportBlock; sources: Map<string, TranscriptSegment> }) {
  const evidence = block.source_segment_ids.map((id) => sources.get(id)).filter((s): s is TranscriptSegment => !!s);
  return <article className={`report-card report-card--${block.type}`}>
    {block.title && <h4>{block.title}</h4>}
    <p>{block.text}</p>
    {block.detail && <details><summary>{t("Details")}</summary><p>{block.detail}</p></details>}
    {evidence.length > 0 && <details className="report-card__evidence"><summary>{t("View transcript sources")}</summary>
      {evidence.map((segment) => <blockquote key={segment.id}>{segment.text}</blockquote>)}
    </details>}
  </article>;
}

export function ReportView({ report, transcript }: Props) {
  const { document: doc } = report;
  const root = useRef<HTMLDivElement>(null);
  const sectionRefs = useRef<Array<HTMLElement | null>>([]);
  const [presentation, setPresentation] = useState(false);
  const [slide, setSlide] = useState(0);
  const [message, setMessage] = useState("");
  const sources = new Map(transcript.map((segment) => [segment.id, segment]));
  const copy = async (text: string) => {
    try { await navigator.clipboard.writeText(text); setMessage(t("Copied")); }
    catch { setMessage(t("Copy failed")); }
  };
  const sectionText = (index: number) => {
    const section = doc.sections[index];
    return [section.title, ...section.blocks.map((b) => [b.title, b.text, b.detail].filter(Boolean).join("\n"))].join("\n\n");
  };
  const scrollToSection = (index: number) => sectionRefs.current[index]?.scrollIntoView({ behavior: "smooth", block: "start" });
  const currentSlide = Math.min(slide, Math.max(0, doc.sections.length - 1));
  return <div className="report-view" ref={root}>
    <header className="report-hero">
      <div className="report-eyebrow">{t("MEETING REPORT")} · {t("Revision {0}", report.revision)}</div>
      <h2>{doc.title}</h2>
      {doc.executive_summary && <p className="report-hero__summary">{doc.executive_summary}</p>}
      <div className="report-meta">{t("Generated from transcript")} · {new Date(report.created_at).toLocaleString()}</div>
      {doc.warnings.map((warning, i) => <p className="report-warning" role="status" key={i}>{t(warning)}</p>)}
      <div className="report-controls">
        <button type="button" onClick={() => setPresentation(!presentation)}>{presentation ? t("Reading mode") : t("Presentation mode")}</button>
        {presentation && <button type="button" onClick={() => { void root.current?.requestFullscreen?.().catch(() => setMessage(t("Full screen unavailable"))); }}>{t("Full screen")}</button>}
        <button type="button" onClick={() => void copy([doc.title, ...doc.sections.map((_, i) => sectionText(i))].join("\n\n"))}>{t("Copy report")}</button>
        <span role="status" aria-live="polite">{message}</span>
      </div>
      {doc.takeaways.length > 0 && <div className="report-grid">{doc.takeaways.map((block, i) => <Block key={i} block={block} sources={sources} />)}</div>}
    </header>
    {!presentation && <nav className="report-nav" aria-label={t("Report contents")}>
      {doc.sections.map((section, i) => <button key={i} type="button" onClick={() => scrollToSection(i)}>{String(i + 1).padStart(2, "0")} · {section.title}</button>)}
    </nav>}
    {presentation && <div className="report-controls report-pager">
      <button type="button" disabled={currentSlide === 0} onClick={() => setSlide(currentSlide - 1)}>{t("Previous")}</button>
      <span>{currentSlide + 1} / {doc.sections.length}</span>
      <button type="button" disabled={currentSlide >= doc.sections.length - 1} onClick={() => setSlide(currentSlide + 1)}>{t("Next")}</button>
    </div>}
    {doc.sections.map((section, i) => <section hidden={presentation && i !== currentSlide} key={section.id || i} ref={(node) => { sectionRefs.current[i] = node; }} className={`report-section report-section--${section.layout_type}`}>
      <div className="report-eyebrow">PART {String(i + 1).padStart(2, "0")}</div>
      <div className="report-section__heading"><h3>{section.title}</h3><button type="button" onClick={() => void copy(sectionText(i))}>{t("Copy section")}</button></div>
      <details open><summary>{t("Show or hide section")}</summary>
        <div className={`report-grid report-grid--${section.layout_type}`}>{section.blocks.map((block, j) => <Block key={j} block={block} sources={sources} />)}</div>
      </details>
    </section>)}
    {!presentation && <button className="report-top" type="button" onClick={() => root.current?.scrollIntoView({ behavior: "smooth" })}>{t("Back to top")}</button>}
  </div>;
}
