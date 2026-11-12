import { useId, useState } from "react";
import type { ReportBlock, ReportDatum, TranscriptSegment } from "../types";
import { sankeyGeometry } from "./sankeyGeometry";
import { Evidence } from "./reportComponents";

type Props = { block: ReportBlock; sources: Map<string, TranscriptSegment> };
export function Chart({ block, sources }: Props) {
  const [series, setSeries] = useState<string | null>(null);
  const [selected, setSelected] = useState<ReportDatum | null>(null);
  const titleId = useId();
  const all = block.data ?? [];
  const data = all.filter(d => series === null || d.series === series);
  const labels = [...new Set(all.map(d => d.label))];
  const groups = [...new Set(all.map(d => d.series))];
  const isLine = block.type === "LineChart";
  const min = Math.min(0, ...all.map(d => Number(d.value)));
  const max = Math.max(0, ...all.map(d => Number(d.value)));
  const range = max - min || 1;
  const y = (value: number) => 250 - (value - min) / range * 205;
  const x = (label: string) => 70 + (labels.indexOf(label) + .5) * 610 / Math.max(1, labels.length);
  const groupWidth = 610 / Math.max(1, labels.length);
  const barWidth = Math.min(64, groupWidth * .7 / Math.max(1, groups.length));
  return <div className="report-visual">
    {groups.length > 1 && <div className="report-choice-buttons" role="group" aria-label="Series / 數據系列">
      {[null, ...groups].map((group, index) => <button type="button" key={index} className={group === null ? "" : `report-series-key report-series-${(index - 1) % 4}`} aria-pressed={series === group} onClick={() => { setSeries(group); setSelected(null); }}>{group === null ? "All / 全部" : group || "Values / 數值"}</button>)}
    </div>}
    <div className="report-svg-scroll" role="region" aria-label={block.title || block.type} tabIndex={0}><svg viewBox="0 0 720 310" role="group" aria-labelledby={titleId}>
      <title id={titleId}>{block.title || block.type} · {all[0]?.unit} · zero baseline / 零基線</title>
      {[min, max].map((v, i) => <g key={i}><line x1="55" x2="690" y1={y(v)} y2={y(v)} className="report-chart-grid" /><text x="48" y={y(v) + 4} textAnchor="end" className="report-chart-label">{v}</text></g>)}
      <line x1="55" x2="690" y1={y(0)} y2={y(0)} className="report-chart-axis" />
      {isLine && groups.map((group, i) => <polyline key={group} fill="none" className={`report-series-${i % 4}`} strokeWidth="2" points={data.filter(d => d.series === group).map(d => `${x(d.label)},${y(Number(d.value))}`).join(" ")} />)}
      {data.map((d, i) => {
        const groupIndex = groups.indexOf(d.series);
        const cx = x(d.label) + (isLine ? 0 : (groupIndex - (groups.length - 1) / 2) * barWidth);
        const label = `${d.series} ${d.label}: ${d.value} ${d.unit}`;
        const props = { tabIndex: 0, role: "button", "aria-label": label, onClick: () => setSelected(d), onKeyDown: (e: React.KeyboardEvent<SVGElement>) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSelected(d); } } };
        return <g key={i} className={`report-series-${groupIndex % 4}`}>
          {isLine ? <circle {...props} cx={cx} cy={y(Number(d.value))} r="6"><title>{label}</title></circle>
            : <rect {...props} x={cx - barWidth / 2} y={Math.min(y(0), y(Number(d.value)))} width={barWidth * .9} height={Math.max(2, Math.abs(y(Number(d.value)) - y(0)))} rx="3"><title>{label}</title></rect>}
        </g>;
      })}
      {labels.map(label => <text key={label} x={x(label)} y="278" textAnchor="middle" className="report-chart-label"><title>{label}</title>{label.length > 12 ? `${label.slice(0, 11)}…` : label}</text>)}
    </svg></div>
    <p className="report-chart-caption">{all[0]?.unit} · Select a mark to inspect its source / 選取圖形查看來源</p>
    {selected && <div className="report-selected" role="status"><strong>{selected.series} {selected.label}: {selected.value} {selected.unit}</strong><p>{selected.evidence_quote}</p><Evidence ids={selected.source_segment_ids} sources={sources} /></div>}
    <details><summary>Data and sources / 數據及來源</summary><div className="report-table-scroll"><table><thead><tr><th>Series / 系列</th><th>Observation / 項目</th><th>Value / 數值</th></tr></thead><tbody>
      {all.map((d, i) => <tr key={i}><td>{d.series}</td><th scope="row">{d.label}</th><td>{d.value} {d.unit}<Evidence ids={d.source_segment_ids} sources={sources} /></td></tr>)}
    </tbody></table></div></details>
  </div>;
}

export function Graph({ block, sources }: Props) {
  const titleId = useId();
  const markerId = useId().replace(/:/g, "");
  const [selected, setSelected] = useState<number | null>(null);
  const nodes = block.nodes ?? [], edges = block.edges ?? [];
  const layered = ["DecisionTree", "SankeyDiagram"].includes(block.type);
  const levels = new Map(nodes.map(n => [n.id, 0]));
  if (layered) for (let round = 0; round < nodes.length; round++) for (const edge of edges) levels.set(edge.target, Math.max(levels.get(edge.target) ?? 0, (levels.get(edge.source) ?? 0) + 1));
  const maxLevel = Math.max(1, ...levels.values());
  const sankey = block.type === "SankeyDiagram" ? sankeyGeometry(block) : null;
  const height = sankey?.height ?? Math.max(320, Math.ceil(nodes.length / 3) * 110);
  const position = (id: string): [number, number] => {
    const index = nodes.findIndex(n => n.id === id);
    if (layered) {
      const level = levels.get(id) ?? 0;
      const peers = nodes.filter(n => levels.get(n.id) === level);
      return [90 + level / maxLevel * 540, (peers.findIndex(n => n.id === id) + 1) / (peers.length + 1) * height];
    }
    const angle = -Math.PI / 2 + index / nodes.length * 2 * Math.PI;
    return [360 + Math.cos(angle) * 255, height / 2 + Math.sin(angle) * (height / 2 - 55)];
  };
  return <div className="report-visual">
    <div className="report-svg-scroll" role="region" aria-label={block.title || block.type} tabIndex={0}><svg viewBox={`0 0 720 ${height}`} role="group" aria-labelledby={titleId}>
      <title id={titleId}>{block.title || block.type}</title>
      <defs><marker id={markerId} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" /></marker></defs>
      {sankey && sankey.ribbons.map((ribbon, i) => <g key={i} className={`report-sankey-ribbon report-series-${i % 4}`}><path d={ribbon.path}><title>{edges[i].value} {edges[i].unit} · {edges[i].evidence_quote}</title></path><text x={ribbon.labelX} y={ribbon.labelY} textAnchor="middle">{edges[i].value} {edges[i].unit}</text></g>)}
      {!sankey && edges.map((edge, i) => {
        const [x1, y1] = position(edge.source), [x2, y2] = position(edge.target);
        const dx = x2 - x1, dy = y2 - y1, length = Math.hypot(dx, dy) || 1;
        const clearance = Math.min(dx ? 70 * length / Math.abs(dx) : Infinity, dy ? 23 * length / Math.abs(dy) : Infinity) + 6;
        return <g key={i} className={`report-edge report-edge--${edge.relation}`}>
          <line x1={x1 + dx / length * clearance} y1={y1 + dy / length * clearance} x2={x2 - dx / length * clearance} y2={y2 - dy / length * clearance} strokeWidth="2" markerEnd={edge.relation !== "related" ? `url(#${markerId})` : undefined} />
          <text x={(x1 + x2) / 2} y={(y1 + y2) / 2 - 7} textAnchor="middle" className="report-chart-label">{edge.value ? `${edge.value} ${edge.unit}` : edge.label.slice(0, 18)}</text>
        </g>;
      })}
      {nodes.map((node, i) => {
        const box = sankey?.nodes.get(node.id);
        const [x, y] = box ? [box.x, box.y + box.height / 2] : position(node.id);
        return <g key={node.id} tabIndex={0} role="button" aria-label={node.label} className="report-graph-node" onClick={() => setSelected(i)} onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSelected(i); } }}>
          <title>{node.label}: {node.text}</title><rect x={box ? box.x : x - 70} y={box ? box.y : y - 23} width={box ? 14 : 140} height={box ? Math.max(2,box.height) : 46} rx={box ? 2 : 8} /><text x={box ? (x > 580 ? x - 8 : x + 22) : x} y={y + 5} textAnchor={box ? (x > 580 ? "end" : "start") : "middle"}>{node.label.length > 13 ? `${node.label.slice(0, 12)}…` : node.label}</text>
        </g>;
      })}
    </svg></div>
    {selected !== null && nodes[selected] && <div className="report-selected" role="status"><strong>{nodes[selected].label}</strong><p>{nodes[selected].text}</p><Evidence ids={nodes[selected].source_segment_ids} sources={sources} /></div>}
    <details><summary>Relationships and sources / 關係及來源</summary>
      {nodes.map(node => <div key={node.id}><strong>{node.label}</strong><p>{node.text}</p><Evidence ids={node.source_segment_ids} sources={sources} /></div>)}
      {edges.map((edge, i) => <div key={i} className="report-relationship"><strong>{nodes.find(n => n.id === edge.source)?.label} {edge.relation === "related" ? "—" : "→"} {nodes.find(n => n.id === edge.target)?.label}</strong><p>{edge.label} {edge.value} {edge.unit}</p><blockquote>{edge.evidence_quote}</blockquote><Evidence ids={edge.source_segment_ids} sources={sources} /></div>)}
    </details>
  </div>;
}

export function Matrix({ block, sources }: Props) {
  const cells = block.cells ?? [];
  const rows = [...new Set(cells.map(c => c.row))], columns = [...new Set(cells.map(c => c.column))];
  const values = cells.map(c => Number(c.value));
  const min = Math.min(...values), range = Math.max(...values) - min || 1;
  const heatmap = block.type === "Heatmap";
  return <div className="report-table-scroll" role="region" aria-label={block.title || block.type} tabIndex={0}>
    {heatmap && <p className="report-chart-caption">{min} → {Math.max(...values)} {cells[0]?.unit} · Dark → bright / 深 → 淺；— = 未提及</p>}
    <table><thead><tr><th scope="col">{block.title}</th>{columns.map(col => <th scope="col" key={col}>{col}</th>)}</tr></thead><tbody>
      {rows.map(row => <tr key={row}><th scope="row">{row}</th>{columns.map(col => {
        const cell = cells.find(c => c.row === row && c.column === col);
        return <td key={col} style={heatmap && cell ? { backgroundColor: `rgba(76,201,170,${.08 + (Number(cell.value) - min) / range * .3})` } : undefined}>{cell ? <>{cell.value} {cell.unit}<Evidence ids={cell.source_segment_ids} sources={sources} /></> : <span aria-label="Not stated / 未提及">—</span>}</td>;
      })}</tr>)}
    </tbody></table>
  </div>;
}
