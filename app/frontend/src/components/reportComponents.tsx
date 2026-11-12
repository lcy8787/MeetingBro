import { useState } from "react";
import { t } from "../i18n";
import type { ReportBlock, TranscriptSegment } from "../types";
import { Chart, Graph, Matrix } from "./reportVisuals";
import { SelectionTabs } from "./reportLayout";

type Sources = Map<string, TranscriptSegment>;
export function blockText(block: ReportBlock): string {
  return [block.title, block.speaker_id, block.text, block.detail,
    ...(block.options ?? []).map(option => `${option.label}\n${option.text}`),
    ...(block.data ?? []).map(d => `${d.series} ${d.label}: ${d.value} ${d.unit}`),
    ...(block.nodes ?? []).map(n => `${n.label}: ${n.text}`),
    ...(block.edges ?? []).map(e => `${block.nodes?.find(n => n.id === e.source)?.label} → ${block.nodes?.find(n => n.id === e.target)?.label}: ${e.label} ${e.value} ${e.unit}\n${e.evidence_quote}`),
    ...(block.cells ?? []).map(c => `${c.row} | ${c.column}: ${c.value} ${c.unit}`),
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
export function Choices({ block, sources }: ContentProps) {
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

const ALIASES: Record<string, string> = {
  HeroSection: "hero", SectionHeader: "heading", EditorialText: "paragraph", SummaryCard: "concept",
  InsightCallout: "insight", ConclusionPanel: "conclusion", ComparisonCards: "comparison",
  ComparisonMatrix: "table", SpecificationTable: "table", DataTable: "table", ProsCons: "pros_cons",
  PerspectiveCards: "comparison", Timeline: "timeline", Stepper: "step", ProcessFlow: "step",
  KeyMetrics: "statistic", DecisionPanel: "decision", ActionItems: "action", OpenQuestions: "questions",
  SpeakerQuote: "quote", TranscriptReference: "reference",
};
function Disclosure({ block, sources }: ContentProps) {
  return <div className="report-disclosures">{block.options?.map((option, i) => <details key={i}><summary>{option.label}</summary><p>{option.text}</p><Evidence ids={option.source_segment_ids} sources={sources} /></details>)}</div>;
}
function ActionList({ block, sources }: ContentProps) {
  return <ul className="report-action-list">{block.options?.map((option, i) => <li key={i}><label className="report-action"><input type="checkbox" /><span><strong>{option.label}</strong> {option.text}</span></label><Evidence ids={option.source_segment_ids} sources={sources} /></li>)}</ul>;
}
function Tabs({ block, sources }: ContentProps) {
  return <SelectionTabs labels={(block.options ?? []).map(o => o.label)}>{(block.options ?? []).map((o, i) => <div key={i}><p>{o.text}</p><Evidence ids={o.source_segment_ids} sources={sources} /></div>)}</SelectionTabs>;
}
const COMPONENT_REGISTRY: Record<string, (props: ContentProps) => JSX.Element> = {
  table: Table, timeline: Items, step: Items, pros_cons: Items, statistic: Items,
  comparison: props => (props.block.options?.length ?? 0) > 3 ? <Choices {...props} /> : <Items {...props} />,
  DataBars: Chart, BarChart: Chart, LineChart: Chart, InteractiveChart: Chart,
  KnowledgeGraph: Graph, ArgumentMap: Graph, DecisionTree: Graph, CausalFlow: Graph, SankeyDiagram: Graph,
  RiskMatrix: Matrix, Heatmap: Matrix, Accordion: Disclosure, ExpandableDetails: Disclosure, TabGroup: Tabs,
  action: ActionList, questions: Items, decision: Items, reference: Items,
};

export function ReportBlockView({ block, sources }: ContentProps) {
  const [done, setDone] = useState(false);
  const kind = ALIASES[block.type] ?? block.type;
  const normalized = { ...block, type: kind as ReportBlock["type"] };
  const Component = COMPONENT_REGISTRY[kind] ?? Choices;
  const visual = !!(block.data?.length || block.nodes?.length || block.cells?.length);
  const full = ["paragraph", "quote", "step", "timeline", "table", "conclusion", "comparison", "pros_cons", "hero", "heading", "decision"].includes(kind)
    || block.text.length + block.detail.length > 200 || !!block.options?.length || visual;
  return <article className={`report-block report-block--${kind}${full ? " report-block--wide" : ""}`}>
    {block.title && <h4>{block.title}</h4>}
    {kind === "action" && !block.options?.length ? <label className="report-action"><input type="checkbox" checked={done} onChange={e => setDone(e.target.checked)} /><span className={done ? "is-done" : ""}>{block.text}</span></label>
      : kind === "quote" ? <blockquote><p>{block.text}</p>{block.speaker_id && <cite>{block.speaker_id}</cite>}</blockquote> : <p>{block.text}</p>}
    {(kind === "table" || visual || !!block.options?.length) && <Component block={normalized} sources={sources} />}
    {block.detail && <details><summary>{t("Details")}</summary><p>{block.detail}</p></details>}
    <Evidence ids={block.source_segment_ids} sources={sources} />
  </article>;
}
