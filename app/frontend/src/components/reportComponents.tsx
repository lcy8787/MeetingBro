import { useState } from "react";
import { t } from "../i18n";
import type { ReportBlock, TranscriptSegment } from "../types";

type Sources = Map<string, TranscriptSegment>;
export function blockText(block: ReportBlock): string {
  return [block.title, block.text, block.detail,
    ...(block.options ?? []).map(option => `${option.label}\n${option.text}`),
    block.columns?.join(" | "), ...(block.rows ?? []).map(row => row.cells.join(" | ")),
  ].filter(Boolean).join("\n");
}

export function Evidence({ ids, sources }: { ids: string[]; sources: Sources }) {
  const evidence = [...new Set(ids)].map(id => sources.get(id)).filter((s): s is TranscriptSegment => !!s);
  if (!evidence.length) return null;
  return <details className="report-evidence"><summary>{t("View transcript sources")}</summary>
    {evidence.map(segment => <blockquote key={segment.id}>
      <small>{segment.speaker_id ? `${segment.speaker_id} · ` : ""}{Math.floor(segment.start_time / 60)}:{String(Math.floor(segment.start_time % 60)).padStart(2, "0")}</small>
      <p>{segment.text}</p>
    </blockquote>)}
  </details>;
}

type ContentProps = { block: ReportBlock; sources: Sources };
function Choices({ block, sources }: ContentProps) {
  const [choice, setChoice] = useState(0);
  const selected = block.options?.[choice];
  return <div className="report-interaction">
    <div className="report-choice-buttons" role="group" aria-label={block.title || t("Explore options")}>
      {block.options?.map((option, index) => <button key={index} type="button" aria-pressed={index === choice} onClick={() => setChoice(index)}>{option.label}</button>)}
    </div>
    <div className="report-choice-result" role="status" aria-live="polite"><strong>{selected?.label}</strong><p>{selected?.text}</p></div>
    {selected && <Evidence ids={selected.source_segment_ids} sources={sources} />}
  </div>;
}
function Items({ block, sources }: ContentProps) {
  const ordered = block.type === "timeline" || block.type === "step";
  const Tag = ordered ? "ol" : "ul";
  return <Tag className={`report-items report-items--${block.type} report-items--count-${Math.min(3, block.options?.length ?? 0)}`}>
    {block.options?.map((option, i) => <li key={i}><strong>{option.label}</strong><p>{option.text}</p>
      <Evidence ids={option.source_segment_ids} sources={sources} /></li>)}
  </Tag>;
}
function Table({ block, sources }: ContentProps) {
  return <div className="report-table-scroll" role="region" aria-label={block.title || t("Details")} tabIndex={0}><table>
    <thead><tr>{block.columns?.map((column, i) => <th scope="col" key={i}>{column}</th>)}</tr></thead>
    <tbody>{block.rows?.map((row, i) => <tr key={i}>{row.cells.map((cell, j) => j === 0
      ? <th scope="row" key={j}>{cell}<Evidence ids={row.source_segment_ids} sources={sources} /></th>
      : <td key={j}>{cell}</td>)}</tr>)}</tbody>
  </table></div>;
}

// Layout decisions are semantic component choices, not searches for keywords in text.
const COMPONENT_REGISTRY: Partial<Record<ReportBlock["type"], (props: ContentProps) => JSX.Element>> = {
  table: Table, timeline: Items, step: Items, pros_cons: Items, statistic: Items,
  comparison: props => (props.block.options?.length ?? 0) > 3 ? <Choices {...props} /> : <Items {...props} />,
};

export function ReportBlockView({ block, sources }: ContentProps) {
  const [done, setDone] = useState(false);
  const Component = COMPONENT_REGISTRY[block.type] ?? Choices;
  const full = ["paragraph", "quote", "step", "timeline", "table", "conclusion", "comparison", "pros_cons"].includes(block.type)
    || block.text.length + block.detail.length > 200 || !!block.options?.length;
  return <article className={`report-block report-block--${block.type}${full ? " report-block--wide" : ""}`}>
    {block.title && <h4>{block.title}</h4>}
    {block.type === "action" ? <label className="report-action"><input type="checkbox" checked={done} onChange={e => setDone(e.target.checked)} /><span className={done ? "is-done" : ""}>{block.text}</span></label> : <p>{block.text}</p>}
    {(block.type === "table" || !!block.options?.length) && <Component block={block} sources={sources} />}
    {block.detail && <details><summary>{t("Details")}</summary><p>{block.detail}</p></details>}
    <Evidence ids={block.source_segment_ids} sources={sources} />
  </article>;
}
