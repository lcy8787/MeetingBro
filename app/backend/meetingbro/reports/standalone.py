"""Render a standalone report from validated data; report text is always escaped."""
from __future__ import annotations

from html import escape
import base64
import hashlib
from pathlib import Path
from typing import Sequence

from ..schemas import ReportBlock, ReportRevision, TranscriptSegment
from ..report_contracts import CHART_TYPES, GRAPH_TYPES, MATRIX_TYPES, TABLE_TYPES, component_kind
from .visual_html import chart, graph, matrix, visual_text
from .design import validate_layout

_STYLE = """
:root{color-scheme:dark;--bg:#101813;--surface:#1c2720;--raised:#25352d;--text:#e6eee8;--muted:#9cafa2;--accent:#4cc9aa;--border:#33463b}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--text);font:1rem/1.65 system-ui,sans-serif}main{max-width:76rem;margin:auto;padding:clamp(1rem,5vw,4rem);overflow-wrap:anywhere}.eyebrow{color:var(--accent);font-weight:700;letter-spacing:.14em;font-size:.75rem}h1{font-size:clamp(2.4rem,7vw,5rem);line-height:1.1;margin:.7rem 0 1rem}h2{font-size:clamp(1.7rem,4vw,3rem);line-height:1.2;margin:.4rem 0 1rem}.muted{color:var(--muted)}.warning{color:#e3b46c}nav,.controls,.choices{display:flex;gap:.6rem;flex-wrap:wrap;align-items:center}nav{margin:2rem 0;padding-bottom:1.5rem;border-bottom:1px solid var(--border)}button,nav a{background:var(--raised);border:1px solid var(--border);color:var(--text);border-radius:.5rem;padding:.6rem .85rem;text-decoration:none;cursor:pointer;font:inherit}button:disabled{opacity:.4;cursor:not-allowed}button:focus-visible,a:focus-visible,summary:focus-visible,.table-scroll:focus-visible{outline:2px solid var(--accent);outline-offset:3px}section{margin:3rem 0;scroll-margin-top:1rem}.grid{display:grid;margin-top:1rem}summary{cursor:pointer;color:var(--accent);padding:.4rem 0}blockquote{margin:.7rem 0;border-left:2px solid var(--border);padding-left:1rem;color:var(--muted)}[hidden]{display:none!important}.top{margin-top:1rem}.choices button[aria-pressed=true]{background:var(--accent);border-color:var(--accent);color:#07291d;font-weight:700}.action{display:flex;align-items:baseline;gap:.8rem;cursor:pointer}.action input{accent-color:var(--accent)}.action input:checked+span{text-decoration:line-through;opacity:.5}

.grid{grid-template-columns:repeat(3,minmax(0,1fr));gap:1.5rem}.grid.text,.grid.timeline,.grid.step_flow,.grid.conclusion{grid-template-columns:1fr}.grid.timeline{border:0;padding:0;margin:1rem 0}.block{min-width:0;padding:.4rem 0;line-height:1.85}.block.wide{grid-column:1/-1}.block h3,.block h4{margin:0 0 .5rem;color:var(--text)}.block p{white-space:pre-wrap;margin:.4rem 0 .8rem}.block.paragraph>p,.block.argument>p{max-width:76ch}.block.bullet>p{border-left:2px solid var(--border);padding-left:1rem}.block.insight,.block.conclusion{grid-column:1/-1;border-left:3px solid var(--accent);padding:1rem 1.3rem;background:var(--surface)}.block.quote{border-left:2px solid var(--border);padding-left:1.2rem;font-style:italic}.block.statistic>p{font-size:1.3rem;color:var(--accent);font-weight:650}section{border-top:1px solid var(--border);padding-top:2rem}.items{list-style:none;padding:0;margin:1rem 0;display:grid;gap:1rem;grid-template-columns:repeat(3,minmax(0,1fr))}.items li{min-width:0;padding:1rem;background:var(--surface);border-radius:.6rem}.items strong{color:var(--accent)}.items.timeline,.items.step{display:block;border-left:2px solid var(--border);padding-left:1.5rem}.items.timeline li,.items.step li{position:relative;background:transparent;padding:.25rem 0 1.3rem}.items.timeline li:before,.items.step li:before{content:'';position:absolute;left:-1.9rem;top:.7rem;width:.65rem;height:.65rem;border-radius:50%;background:var(--accent)}.items.step li:not(:last-child):after{content:'↓';color:var(--muted)}.table-scroll{overflow-x:auto;max-width:100%;margin:1rem 0}table{border-collapse:collapse;width:100%;text-align:left;font-size:.95rem}th,td{border-bottom:1px solid var(--border);padding:.8rem;vertical-align:top;min-width:8rem;white-space:pre-wrap}thead th{color:var(--accent);background:var(--surface)}tbody th{font-weight:500}.evidence{font-size:.8rem;color:var(--muted);font-style:normal}.evidence p{font-size:.85rem}.interaction{border:0;padding:0;margin:1rem 0}[data-choice-panel]{border:0;border-left:2px solid var(--accent);border-radius:0;background:transparent;margin-top:.8rem;padding:.5rem 1rem}header>p{white-space:pre-wrap}@media(max-width:1000px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:600px){.grid,.grid.comparison,.items{grid-template-columns:1fr}}@media print{.block,.items li{break-inside:avoid}}

.items.count-2{grid-template-columns:repeat(2,minmax(0,1fr))}.items.count-1{grid-template-columns:1fr}.items.statistic p{color:var(--accent);font-size:1.25rem}.grid.timeline>.block{border-left:2px solid var(--border);padding-left:1.2rem}.grid.step_flow>.block+.block:before{content:'↓';color:var(--muted)}@media(max-width:600px){.items.count-2{grid-template-columns:1fr}}@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}@media print{:root{color-scheme:light}body{background:white;color:black}.controls,nav,.top{display:none}section[hidden]{display:block!important}.block,.items li{background:white;color:black}}
"""
_STYLE += Path(__file__).with_name("design.css").read_text(encoding="utf-8")
_STYLE += ":root{--report-bg:var(--bg);--report-surface:var(--surface);--report-text:var(--text);--report-muted:var(--muted);--report-border:var(--border);--report-accent:var(--accent)}.report-view{container:report / inline-size}.report-view .report-choice-buttons{display:flex;gap:.5rem;flex-wrap:wrap}.report-block{min-width:0}.report-selected:empty{display:none}"

_SCRIPT = """
(() => {
  const status = document.getElementById('copy-status');
  async function copy(text) {
    try {
      await navigator.clipboard.writeText(text);
      status.textContent = 'Copied / 已複製'; return;
    } catch (_) {}
    const field = document.createElement('textarea'); field.value = text;
    field.setAttribute('readonly', ''); field.style.position = 'fixed'; field.style.left = '-9999px';
    document.body.appendChild(field); field.select();
    let ok = false; try { ok = document.execCommand('copy'); } catch (_) {}
    field.remove(); status.textContent = ok ? 'Copied / 已複製' : 'Copy unavailable / 請手動選取文字';
  }
  document.querySelectorAll('[data-copy]').forEach(button => button.addEventListener('click', () => {
    const target = document.getElementById(button.dataset.copy);
    if (target) void copy(target.content.textContent);
  }));
  document.querySelectorAll('[data-choices]').forEach(group => {
    const buttons = [...group.querySelectorAll('button')];
    const panels = [...group.querySelectorAll('[data-choice-panel]')];
    buttons.forEach((button, index) => button.addEventListener('click', () => {
      buttons.forEach((item, i) => item.setAttribute('aria-pressed', String(i === index)));
      panels.forEach((panel, i) => { panel.hidden = i !== index; });
    }));
  });
  document.querySelectorAll('[data-layout-tabs]').forEach(group => {
    const buttons = [...group.querySelector('[role=tablist]').children];
    const panels = [...group.children].filter(item => item.matches('[role=tabpanel]'));
    function select(index) {
      buttons.forEach((button, i) => { button.setAttribute('aria-selected', String(i === index)); button.tabIndex = i === index ? 0 : -1; });
      panels.forEach((panel, i) => { panel.hidden = i !== index; });
    }
    buttons.forEach((button, i) => {
      button.addEventListener('click', () => select(i));
      button.addEventListener('keydown', event => {
        const index = event.key === 'ArrowRight' ? (i+1)%buttons.length : event.key === 'ArrowLeft' ? (i-1+buttons.length)%buttons.length : event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length-1 : null;
        if (index !== null) { event.preventDefault(); select(index); buttons[index].focus(); }
      });
    });
  });
  document.querySelectorAll('[data-visual]').forEach(group => {
    group.querySelectorAll('[data-observation]').forEach(mark => {
      function select() { group.querySelectorAll('[data-observation-panel]').forEach(panel => { panel.hidden = panel.dataset.observationPanel !== mark.dataset.observation; }); }
      mark.addEventListener('click', select);
      mark.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(); } });
    });
    group.querySelectorAll('[data-series-select]').forEach(button => button.addEventListener('click', () => {
      const selected = button.dataset.seriesSelect;
      group.querySelectorAll('[data-series-select]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
      group.querySelectorAll('[data-series]').forEach(mark => { mark.toggleAttribute('hidden', selected !== '-1' && mark.dataset.series !== selected); });
      group.querySelectorAll('[data-observation-panel]').forEach(panel => { panel.hidden = true; });
    }));
  });
})();
"""


def _evidence(ids: Sequence[str], sources: dict[str, TranscriptSegment]) -> str:
    evidence = ''.join(f'<blockquote><small>{escape(sources[key].speaker_id or "")} · '
                       f'{int(sources[key].start_time // 60)}:{int(sources[key].start_time % 60):02d}</small>'
                       f'<p>{escape(sources[key].text)}</p></blockquote>' for key in dict.fromkeys(ids) if key in sources)
    return f'<details class="evidence"><summary>Transcript sources / 逐字稿來源</summary>{evidence}</details>' if evidence else ''


def _table(block: ReportBlock, sources: dict[str, TranscriptSegment]) -> str:
    headings = ''.join(f'<th scope="col">{escape(column)}</th>' for column in block.columns)
    rows = []
    for row in block.rows:
        cells = ''.join(f'<td>{escape(cell)}</td>' for cell in row.cells[1:])
        rows.append(f'<tr><th scope="row">{escape(row.cells[0])}{_evidence(row.source_segment_ids, sources)}</th>{cells}</tr>')
    return f'<div class="table-scroll" role="region" aria-label="{escape(block.title or "Details", quote=True)}" tabindex="0"><table><thead><tr>{headings}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


def _items(block: ReportBlock, sources: dict[str, TranscriptSegment]) -> str:
    tag = 'ol' if component_kind(block.type) in {'timeline', 'step'} else 'ul'
    items = ''.join(f'<li><strong>{escape(option.label)}</strong><p>{escape(option.text)}</p>'
                    f'{_evidence(option.source_segment_ids, sources)}</li>' for option in block.options)
    return f'<{tag} class="items {component_kind(block.type)} count-{min(3, len(block.options))}">{items}</{tag}>'


def _choices(block: ReportBlock, sources: dict[str, TranscriptSegment]) -> str:
    buttons = ''.join(f'<button type="button" aria-pressed="{str(i == 0).lower()}">{escape(option.label)}</button>' for i, option in enumerate(block.options))
    panels = []
    for i, option in enumerate(block.options):
        hidden = '' if i == 0 else ' hidden'
        panels.append(f'<div data-choice-panel{hidden}><strong>{escape(option.label)}</strong><p>{escape(option.text)}</p>{_evidence(option.source_segment_ids, sources)}</div>')
    return f'<div class="interaction" data-choices><div class="choices" role="group" aria-label="{escape(block.title or "Explore options", quote=True)}">{buttons}</div><div aria-live="polite">{"".join(panels)}</div></div>'


def _disclosures(block, sources):
    return ''.join(f'<details><summary>{escape(o.label)}</summary><p>{escape(o.text)}</p>{_evidence(o.source_segment_ids, sources)}</details>' for o in block.options)


def _tabs(labels, panels, uid):
    buttons = ''.join(f'<button type="button" role="tab" id="{uid}-tab-{i}" aria-controls="{uid}-panel-{i}" aria-selected="{str(i == 0).lower()}" tabindex="{0 if i == 0 else -1}">{escape(label)}</button>' for i, label in enumerate(labels))
    content = ''.join(f'<div role="tabpanel" id="{uid}-panel-{i}" aria-labelledby="{uid}-tab-{i}" tabindex="0"' + ('' if i == 0 else ' hidden') + f'>{panel}</div>' for i, panel in enumerate(panels))
    return f'<div data-layout-tabs><div class="report-choice-buttons choices" role="tablist" aria-label="Views / 檢視">{buttons}</div>{content}</div>'


def _block_text(block: ReportBlock) -> str:
    return '\n'.join(filter(None, [block.title, block.speaker_id, block.text, block.detail,
        *(option.label + "\n" + option.text for option in block.options),
        ' | '.join(block.columns), *(' | '.join(row.cells) for row in block.rows), *visual_text(block)]))


def _block(block: ReportBlock, sources: dict[str, TranscriptSegment], uid='block') -> str:
    kind = component_kind(block.type)
    title = f'<h4>{escape(block.title)}</h4>' if block.title else ''
    detail = f'<details><summary>Details / 詳情</summary><p>{escape(block.detail)}</p></details>' if block.detail else ''
    text = f'<p>{escape(block.text)}</p>'
    if kind == 'action' and not block.options:
        text = f'<label class="action report-action"><input type="checkbox"><span>{escape(block.text)}</span></label>'
    elif kind == 'quote':
        text = f'<blockquote><p>{escape(block.text)}</p><cite>{escape(block.speaker_id)}</cite></blockquote>'
    content = ''
    if block.type in CHART_TYPES and block.data:
        content = chart(block, sources, _evidence)
    elif block.type in GRAPH_TYPES and block.nodes:
        content = graph(block, sources, _evidence, uid + '-arrow')
    elif block.type in MATRIX_TYPES and block.cells:
        content = matrix(block, sources, _evidence)
    elif block.type in TABLE_TYPES:
        content = _table(block, sources)
    elif block.type in {'Accordion', 'ExpandableDetails'}:
        content = _disclosures(block, sources)
    elif block.type == 'TabGroup':
        content = _tabs([o.label for o in block.options], [f'<p>{escape(o.text)}</p>{_evidence(o.source_segment_ids, sources)}' for o in block.options], uid)
    elif kind == 'action' and block.options:
        content = '<ul class="report-action-list">' + ''.join(f'<li><label class="action report-action"><input type="checkbox"><span><strong>{escape(o.label)}</strong> {escape(o.text)}</span></label>{_evidence(o.source_segment_ids, sources)}</li>' for o in block.options) + '</ul>'
    elif block.options:
        content = _items(block, sources) if kind in {'timeline', 'step', 'pros_cons', 'statistic', 'questions', 'decision', 'reference'} or kind == 'comparison' and len(block.options) <= 3 else _choices(block, sources)
    full = kind in {'paragraph', 'quote', 'step', 'timeline', 'table', 'conclusion', 'comparison', 'pros_cons', 'hero', 'heading', 'decision'} or len(block.text + block.detail) > 200 or bool(block.options) or bool(block.data or block.nodes or block.cells)
    width = ' wide' if full else ''
    return f'<article class="block {kind}{width} report-block report-block--{kind}">{title}{text}{content}{detail}{_evidence(block.source_segment_ids, sources)}</article>'


def _layout(section, sources, uid):
    validate_layout(section)
    def label(node):
        if node.op == 'block':
            block = section.blocks[node.block_index]
            return block.title or block.type
        return label(node.children[0])
    def render(node, path):
        if node.op == 'block':
            content = _block(section.blocks[node.block_index], sources, path)
        elif node.op == 'tabs':
            content = _tabs([label(child) for child in node.children], [render(child, f'{path}-{i}') for i, child in enumerate(node.children)], path)
        elif node.op == 'accordion':
            content = ''.join(f'<details><summary>{escape(label(child))}</summary>{render(child, f"{path}-{i}")}</details>' for i, child in enumerate(node.children))
        else:
            content = ''.join(render(child, f'{path}-{i}') for i, child in enumerate(node.children))
        return f'<div class="report-layout report-layout--{node.op} report-emphasis--{node.emphasis}" style="--layout-span:{node.span};--layout-columns:{node.columns}">{content}</div>'
    if section.layout:
        return render(section.layout, uid)
    return f'<div class="grid {section.layout_type}">' + ''.join(_block(block, sources, f'{uid}-{i}') for i, block in enumerate(section.blocks)) + '</div>'


def write_standalone_report(path: Path, report: ReportRevision, transcript: Sequence[TranscriptSegment]) -> None:
    doc = report.document
    if doc.generation_method != 'llm':
        raise ValueError('Only model-generated reports can be exported')
    sources = {segment.id: segment for segment in transcript}
    nav = ''.join(f'<a href="#part-{i}">{i:02d} · {escape(section.title)}</a>' for i, section in enumerate(doc.sections, 1))
    sections = []
    copy_sections = []
    for i, section in enumerate(doc.sections, 1):
        cards = _layout(section, sources, f'section-{i}')
        copy_text = '\n\n'.join([section.title, *(_block_text(block) for block in section.blocks)])
        copy_sections.append(copy_text)
        sections.append(f'<section id="part-{i}" data-section><div class="eyebrow">PART {i:02d}</div>'
                        f'<h2>{escape(section.title)}</h2><button type="button" data-copy="copy-{i}">Copy section / 複製章節</button>'
                        f'<details open><summary>Show or hide section / 展開或收合章節</summary>'
                        f'<div id="content-{i}">{cards}</div></details></section>')
    takeaways = ''.join(_block(block, sources, f'takeaway-{i}') for i, block in enumerate(doc.takeaways))
    warnings = ''.join(f'<p class="warning">{escape(w)}</p>' for w in doc.warnings)
    copies = ''.join(f'<template id="copy-{i}">{escape(text)}</template>' for i, text in enumerate(copy_sections, 1))
    full_copy = '\n\n'.join(filter(None, [doc.title, doc.executive_summary, *(_block_text(block) for block in doc.takeaways), *copy_sections]))
    copies += f'<template id="copy-full">{escape(full_copy)}</template>'
    script_hash = base64.b64encode(hashlib.sha256(_SCRIPT.encode('utf-8')).digest()).decode('ascii')
    html = f'''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'sha256-{script_hash}'">
<title>{escape(doc.title or 'Meeting report')}</title><style>{_STYLE}</style></head><body><main id="report" class="report-view"><header id="top">
<div class="eyebrow">MEETING REPORT / 會議報告</div><h1>{escape(doc.title or 'Meeting report')}</h1><p>{escape(doc.executive_summary)}</p>{_evidence(doc.executive_summary_source_segment_ids, sources)}
<p class="muted">Revision / 版本 {report.revision}</p>{warnings}<div class="controls"><button type="button" data-copy="copy-full">Copy report / 複製報告</button><span id="copy-status" role="status"></span></div>
<div class="grid">{takeaways}</div></header><nav id="toc" aria-label="Report contents / 報告目錄">{nav}</nav>

<div id="report-content">{''.join(sections)}</div><a class="top" href="#top">Back to top / 回到頂部</a>{copies}</main><script>{_SCRIPT}</script></body></html>'''
    path.write_text(html, encoding='utf-8')
