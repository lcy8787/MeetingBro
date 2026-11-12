import { useId, useState } from "react";
import type { LayoutNode, ReportSection, TranscriptSegment } from "../types";
import { ReportBlockView } from "./reportComponents";

export function SelectionTabs({ labels, children }: { labels: string[]; children: React.ReactNode[] }) {
  const [selected, setSelected] = useState(0);
  const base = useId();
  return <div className="report-layout-tabs">
    <div className="report-choice-buttons" role="tablist" aria-label="Views / 檢視">
      {labels.map((label, i) => <button key={i} type="button" role="tab" id={`${base}-tab-${i}`} aria-controls={`${base}-panel-${i}`} aria-selected={selected === i} tabIndex={selected === i ? 0 : -1}
        onClick={() => setSelected(i)} onKeyDown={e => {
          const next = e.key === "ArrowRight" ? (i + 1) % labels.length : e.key === "ArrowLeft" ? (i - 1 + labels.length) % labels.length : e.key === "Home" ? 0 : e.key === "End" ? labels.length - 1 : null;
          if (next !== null) { e.preventDefault(); setSelected(next); document.getElementById(`${base}-tab-${next}`)?.focus(); }
        }}>{label}</button>)}
    </div>
    {children.map((child, i) => <div key={i} id={`${base}-panel-${i}`} role="tabpanel" aria-labelledby={`${base}-tab-${i}`} hidden={selected !== i} tabIndex={0}>{child}</div>)}
  </div>;
}

function validLayout(root: LayoutNode, count: number): boolean {
  const placed: number[] = [];
  let size = 0;
  function walk(node: LayoutNode, depth: number): boolean {
    if (++size > 40 || depth > 3 || !["stack", "grid", "split", "tabs", "accordion", "block"].includes(node.op)) return false;
    if (node.op === "block") {
      if (!Number.isInteger(node.block_index) || node.block_index === null || node.block_index < 0 || node.block_index >= count || node.children.length) return false;
      placed.push(node.block_index); return true;
    }
    return !!node.children.length && node.children.every(child => walk(child, depth + 1));
  }
  return walk(root, 0) && placed.length === count && new Set(placed).size === count;
}

export function ReportSectionContent({ section, sources }: { section: ReportSection; sources: Map<string, TranscriptSegment> }) {
  const layout = section.layout;
  if (!layout || !validLayout(layout, section.blocks.length)) return <div className={`report-grid report-grid--${section.layout_type}`}>{section.blocks.map((block, i) => <ReportBlockView key={i} block={block} sources={sources} />)}</div>;
  function label(node: LayoutNode): string {
    if (node.op === "block" && node.block_index !== null) return section.blocks[node.block_index].title || section.blocks[node.block_index].type;
    return node.children.length ? label(node.children[0]) : section.title;
  }
  function render(node: LayoutNode, key: number): JSX.Element {
    const span = Math.max(1, Math.min(12, Math.round(node.span)));
    const columns = Math.max(1, Math.min(4, Math.round(node.columns)));
    const emphasis = ["quiet", "normal", "strong"].includes(node.emphasis) ? node.emphasis : "normal";
    let content: React.ReactNode;
    if (node.op === "block") content = <ReportBlockView block={section.blocks[node.block_index!]} sources={sources} />;
    else if (node.op === "tabs") content = <SelectionTabs labels={node.children.map(label)}>{node.children.map(render)}</SelectionTabs>;
    else if (node.op === "accordion") content = node.children.map((child, i) => <details key={i}><summary>{label(child)}</summary>{render(child, i)}</details>);
    else content = node.children.map(render);
    return <div key={key} className={`report-layout report-layout--${node.op} report-emphasis--${emphasis}`} style={{ "--layout-span": span, "--layout-columns": columns } as React.CSSProperties}>{content}</div>;
  }
  return render(layout, 0);
}
