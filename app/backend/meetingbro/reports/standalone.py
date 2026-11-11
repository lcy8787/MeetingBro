"""Render a standalone report from validated data; report text is always escaped."""
from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Sequence

from ..schemas import ReportBlock, ReportRevision, TranscriptSegment

_STYLE = '''
:root{color-scheme:dark;--bg:#101813;--surface:#1c2720;--raised:#25352d;--text:#e6eee8;--muted:#9cafa2;--accent:#4cc9aa;--border:#33463b}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--text);font:1rem/1.65 system-ui,sans-serif}main{max-width:76rem;margin:auto;padding:clamp(1rem,5vw,4rem);overflow-wrap:anywhere}.eyebrow{color:var(--accent);font-weight:700;letter-spacing:.14em;font-size:.75rem}h1{font-size:clamp(2.4rem,7vw,5rem);line-height:1.05;margin:.7rem 0 1rem}h2{font-size:clamp(1.7rem,4vw,3rem);line-height:1.15;margin:.4rem 0 1rem}.muted{color:var(--muted)}.warning{color:#e3b46c}nav,.controls{display:flex;gap:.6rem;flex-wrap:wrap;align-items:center}nav{margin:2rem 0;padding-bottom:1.5rem;border-bottom:1px solid var(--border)}button,nav a{background:var(--raised);border:1px solid var(--border);color:var(--text);border-radius:.5rem;padding:.6rem .85rem;text-decoration:none;cursor:pointer;font:inherit}button:disabled{opacity:.4;cursor:not-allowed}button:focus-visible,a:focus-visible,summary:focus-visible{outline:2px solid var(--accent);outline-offset:3px}section{margin:3rem 0;scroll-margin-top:1rem}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,16rem),1fr));gap:1rem}.grid.text,.grid.timeline,.grid.conclusion{grid-template-columns:1fr}.card{background:var(--surface);border:1px solid var(--border);border-radius:1rem;padding:1.2rem;min-width:0}.card h3{color:var(--accent);margin:0 0 .5rem}.card p{white-space:pre-wrap}.card.timeline{border-left:4px solid #55c6a9}.card.statistic{border-top:3px solid var(--accent)}.card.statistic p{font-size:clamp(1.25rem,3vw,2rem);font-weight:700;color:var(--accent)}.grid.comparison{grid-template-columns:repeat(2,minmax(0,1fr))}.grid.comparison>.card:only-child{grid-column:1/-1}.grid.step_flow{counter-reset:step}.grid.step_flow .card:before{counter-increment:step;content:counter(step,decimal-leading-zero);display:block;color:var(--accent);font-weight:700}.grid.timeline{border-left:2px solid var(--border);padding-left:1.2rem;margin-left:.5rem}@media(max-width:600px){.grid.comparison{grid-template-columns:1fr}}.card.conclusion{border-left:4px solid var(--accent);background:var(--raised);font-size:1.2rem}summary{cursor:pointer;color:var(--accent);padding:.4rem 0}blockquote{margin:.7rem 0;border-left:2px solid var(--border);padding-left:1rem;color:var(--muted)}[hidden]{display:none!important}.pager{margin:2rem 0}.top{margin-top:1rem}@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}@media print{:root{color-scheme:light}body{background:white;color:black}.controls,nav,.top{display:none}.card{background:white;color:black;break-inside:avoid}section[hidden]{display:block!important}}
'''
_STYLE += """
.interaction{margin-top:1.4rem;padding:1.2rem;border:1px solid var(--border);border-radius:1rem}.choices{display:flex;gap:.7rem;flex-wrap:wrap}.choices button[aria-pressed=true]{background:var(--accent);border-color:var(--accent);color:#07291d;font-weight:700}[data-choice-panel]{padding:1.2rem;margin-top:1rem;border:1px solid var(--border);border-radius:.8rem;background:var(--bg)}[data-choice-panel] strong{color:var(--accent)}.action{display:flex;align-items:baseline;gap:.8rem;cursor:pointer}.action input{accent-color:var(--accent)}.action input:checked+span{text-decoration:line-through;opacity:.5}.grid.step_flow{grid-template-columns:1fr;max-width:48rem;margin:1rem auto;gap:2rem}.grid.step_flow .card{position:relative;text-align:center;border-color:var(--accent)}.grid.step_flow .card+.card:after{content:'↓';position:absolute;top:-2.2rem;left:50%;font-size:1.5rem;color:var(--muted)}.card h3{border-left:4px solid var(--accent);padding-left:.8rem;color:var(--text)}
"""
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
})();
"""


def _block(block: ReportBlock, sources: dict[str, TranscriptSegment]) -> str:
    title = f'<h3>{escape(block.title)}</h3>' if block.title else ''
    detail = f'<details><summary>Details / 詳情</summary><p>{escape(block.detail)}</p></details>' if block.detail else ''
    ids = dict.fromkeys([*block.source_segment_ids, *(key for option in block.options for key in option.source_segment_ids)])
    evidence = ''.join(f'<blockquote>{escape(sources[key].text)}</blockquote>' for key in ids if key in sources)
    source = f'<details><summary>Transcript sources / 逐字稿來源</summary>{evidence}</details>' if evidence else ''
    text = f'<p>{escape(block.text)}</p>'
    if block.type == 'action':
        text = f'<label class="action"><input type="checkbox"><span>{escape(block.text)}</span></label>'
    interaction = ''
    if block.options:
        buttons = ''.join(f'<button type="button" aria-pressed="{str(i == 0).lower()}">{escape(option.label)}</button>' for i, option in enumerate(block.options))
        panels = []
        for i, option in enumerate(block.options):
            hidden = '' if i == 0 else ' hidden'
            panels.append(f'<div data-choice-panel{hidden}><strong>{escape(option.label)}</strong><p>{escape(option.text)}</p></div>')
        interaction = f'<div class="interaction" data-choices><div class="choices" role="group" aria-label="{escape(block.title or "Explore options", quote=True)}">{buttons}</div><div aria-live="polite">{"".join(panels)}</div></div>'
    return f'<article class="card {escape(block.type, quote=True)}">{title}{text}{interaction}{detail}{source}</article>'


def write_standalone_report(path: Path, report: ReportRevision, transcript: Sequence[TranscriptSegment]) -> None:
    doc = report.document
    if doc.generation_method != 'llm':
        raise ValueError('Only model-generated reports can be exported')
    sources = {segment.id: segment for segment in transcript}
    nav = ''.join(f'<a href="#part-{i}">{i:02d} · {escape(section.title)}</a>' for i, section in enumerate(doc.sections, 1))
    sections = []
    copy_sections = []
    for i, section in enumerate(doc.sections, 1):
        cards = ''.join(_block(block, sources) for block in section.blocks)
        copy_text = '\n\n'.join([section.title, *('\n'.join(filter(None, (block.title, block.text, block.detail, *(option.label + "\n" + option.text for option in block.options)))) for block in section.blocks)])
        copy_sections.append(copy_text)
        sections.append(f'<section id="part-{i}" data-section><div class="eyebrow">PART {i:02d}</div>'
                        f'<h2>{escape(section.title)}</h2><button type="button" data-copy="copy-{i}">Copy section / 複製章節</button>'
                        f'<details open><summary>Show or hide section / 展開或收合章節</summary>'
                        f'<div id="content-{i}" class="grid {escape(section.layout_type, quote=True)}">{cards}</div></details></section>')
    takeaways = ''.join(_block(block, sources) for block in doc.takeaways)
    warnings = ''.join(f'<p class="warning">{escape(w)}</p>' for w in doc.warnings)
    copies = ''.join(f'<template id="copy-{i}">{escape(text)}</template>' for i, text in enumerate(copy_sections, 1))
    full_copy = '\n\n'.join(filter(None, [doc.title, doc.executive_summary, *(block.text for block in doc.takeaways), *copy_sections]))
    copies += f'<template id="copy-full">{escape(full_copy)}</template>'
    html = f'''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
<title>{escape(doc.title or 'Meeting report')}</title><style>{_STYLE}</style></head><body><main id="report"><header id="top">
<div class="eyebrow">MEETING REPORT / 會議報告</div><h1>{escape(doc.title or 'Meeting report')}</h1><p>{escape(doc.executive_summary)}</p>
<p class="muted">Revision / 版本 {report.revision}</p>{warnings}<div class="controls"><button type="button" data-copy="copy-full">Copy report / 複製報告</button><span id="copy-status" role="status"></span></div>
<div class="grid">{takeaways}</div></header><nav id="toc" aria-label="Report contents / 報告目錄">{nav}</nav>

<div id="report-content">{''.join(sections)}</div><a class="top" href="#top">Back to top / 回到頂部</a>{copies}</main><script>{_SCRIPT}</script></body></html>'''
    path.write_text(html, encoding='utf-8')
