import { useRef, useState } from "react";
import { t } from "../i18n";
import type { ReportBlock, ReportRevision, TranscriptSegment } from "../types";

interface Props {
  report: ReportRevision;
  transcript: TranscriptSegment[];
}

function Block({ block, sources }: { block: ReportBlock; sources: Map<string, TranscriptSegment> }) {
  const [choice, setChoice] = useState(0);
  const [done, setDone] = useState(false);
  const selected = block.options?.[choice];
  const sourceIds = [...new Set([...block.source_segment_ids, ...(selected?.source_segment_ids ?? [])])];
  const evidence = sourceIds.map((id) => sources.get(id)).filter((s): s is TranscriptSegment => !!s);
  return <article className={`report-card report-card--${block.type}`}>
    {block.title && <h4>{block.title}</h4>}
    {block.type === "action" ? <label className="report-action"><input type="checkbox" checked={done} onChange={e => setDone(e.target.checked)} /><span className={done ? "is-done" : ""}>{block.text}</span></label> : <p>{block.text}</p>}
    {!!block.options?.length && <div className="report-interaction">
      <div className="report-choice-buttons" role="group" aria-label={block.title || t("Explore options")}>
        {block.options.map((option, index) => <button key={index} type="button" aria-pressed={index === choice} onClick={() => setChoice(index)}>{option.label}</button>)}
      </div>
      <div className="report-choice-result" role="status" aria-live="polite"><strong>{selected?.label}</strong><p>{selected?.text}</p></div>
    </div>}
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
  const [message, setMessage] = useState("");
  const sources = new Map(transcript.map((segment) => [segment.id, segment]));
  const copy = async (text: string) => {
    try { await navigator.clipboard.writeText(text); setMessage(t("Copied")); }
    catch { setMessage(t("Copy failed")); }
  };
  const sectionText = (index: number) => {
    const section = doc.sections[index];
    return [section.title, ...section.blocks.map((b) => [b.title, b.text, b.detail, ...(b.options ?? []).flatMap(option => [option.label, option.text])].filter(Boolean).join("\n"))].join("\n\n");
  };
  if (doc.generation_method !== "llm") return null;
  const scrollToSection = (index: number) => sectionRefs.current[index]?.scrollIntoView({ behavior: "smooth", block: "start" });
  return <div className="report-view" ref={root}>
    <header className="report-hero">
      <div className="report-eyebrow">{t("MEETING REPORT")} · {t("Revision {0}", report.revision)}</div>
      <h2>{doc.title}</h2>
      {doc.executive_summary && <p className="report-hero__summary">{doc.executive_summary}</p>}
      <div className="report-meta">{t("Generated from transcript")} · {new Date(report.created_at).toLocaleString()}</div>
      {doc.warnings.map((warning, i) => <p className="report-warning" role="status" key={i}>{t(warning)}</p>)}
      <div className="report-controls">
        <button type="button" onClick={() => void copy([doc.title, doc.executive_summary, ...doc.takeaways.map((block) => [block.title, block.text, block.detail, ...(block.options ?? []).flatMap(option => [option.label, option.text])].filter(Boolean).join("\n")), ...doc.sections.map((_, i) => sectionText(i))].filter(Boolean).join("\n\n"))}>{t("Copy report")}</button>
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
