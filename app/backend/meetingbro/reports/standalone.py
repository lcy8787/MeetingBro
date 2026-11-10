"""Render a standalone report from validated data; report text is always escaped."""
from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Sequence

from ..schemas import ReportBlock, ReportRevision, TranscriptSegment

_STYLE = '''
:root{color-scheme:dark;--bg:#0f1219;--surface:#1a202c;--raised:#222b3d;--text:#f1f5f9;--muted:#9ca8ba;--accent:#8ba4ff;--border:#344158}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--text);font:1rem/1.65 system-ui,sans-serif}main{max-width:76rem;margin:auto;padding:clamp(1rem,5vw,4rem);overflow-wrap:anywhere}.eyebrow{color:var(--accent);font-weight:700;letter-spacing:.14em;font-size:.75rem}h1{font-size:clamp(2.4rem,7vw,5rem);line-height:1.05;margin:.7rem 0 1rem}h2{font-size:clamp(1.7rem,4vw,3rem);line-height:1.15;margin:.4rem 0 1rem}.muted{color:var(--muted)}.warning{color:#e3b46c}nav,.controls{display:flex;gap:.6rem;flex-wrap:wrap;align-items:center}nav{margin:2rem 0;padding-bottom:1.5rem;border-bottom:1px solid var(--border)}button,nav a{background:var(--raised);border:1px solid var(--border);color:var(--text);border-radius:.5rem;padding:.6rem .85rem;text-decoration:none;cursor:pointer;font:inherit}button:disabled{opacity:.4;cursor:not-allowed}button:focus-visible,a:focus-visible,summary:focus-visible{outline:2px solid var(--accent);outline-offset:3px}section{margin:3rem 0;scroll-margin-top:1rem}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,16rem),1fr));gap:1rem}.grid.text,.grid.timeline,.grid.conclusion{grid-template-columns:1fr}.card{background:var(--surface);border:1px solid var(--border);border-radius:1rem;padding:1.2rem;min-width:0}.card h3{color:var(--accent);margin:0 0 .5rem}.card p{white-space:pre-wrap}.card.timeline{border-left:4px solid #55c6a9}.card.statistic{border-top:3px solid var(--accent)}.card.conclusion{border-left:4px solid var(--accent);background:var(--raised);font-size:1.2rem}summary{cursor:pointer;color:var(--accent);padding:.4rem 0}blockquote{margin:.7rem 0;border-left:2px solid var(--border);padding-left:1rem;color:var(--muted)}[hidden]{display:none!important}.pager{margin:2rem 0}.top{margin-top:1rem}@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}@media print{:root{color-scheme:light}body{background:white;color:black}.controls,nav,.top{display:none}.card{background:white;color:black;break-inside:avoid}section[hidden]{display:block!important}}
'''
_SCRIPT = '''
(()=>{const sections=[...document.querySelectorAll('[data-section]')];let mode=false,index=0;const modeButton=document.getElementById('mode'),pager=document.getElementById('pager'),prev=document.getElementById('prev'),next=document.getElementById('next'),counter=document.getElementById('counter'),nav=document.getElementById('toc'),full=document.getElementById('fullscreen');function render(){sections.forEach((s,i)=>s.hidden=mode&&i!==index);pager.hidden=!mode;nav.hidden=mode;full.hidden=!mode;modeButton.textContent=mode?'Reading mode / 閱讀模式':'Presentation / 簡報模式';counter.textContent=(index+1)+' / '+sections.length;prev.disabled=index===0;next.disabled=index>=sections.length-1}modeButton.addEventListener('click',()=>{mode=!mode;render()});prev.addEventListener('click',()=>{index=Math.max(0,index-1);render()});next.addEventListener('click',()=>{index=Math.min(sections.length-1,index+1);render()});full.addEventListener('click',()=>{if(document.documentElement.requestFullscreen)document.documentElement.requestFullscreen().catch(()=>{})});document.addEventListener('keydown',e=>{if(!mode||/INPUT|TEXTAREA/.test(e.target.tagName))return;if(e.key==='ArrowRight'){index=Math.min(sections.length-1,index+1);render()}if(e.key==='ArrowLeft'){index=Math.max(0,index-1);render()}});async function copy(text){try{await navigator.clipboard.writeText(text);document.getElementById('copy-status').textContent='Copied / 已複製'}catch{document.getElementById('copy-status').textContent='Copy unavailable / 無法複製，請手動選取文字'}}document.querySelectorAll('[data-copy]').forEach(b=>b.addEventListener('click',()=>{const target=document.getElementById(b.dataset.copy);if(target)copy(target.innerText)}));render()})();
'''


def _block(block: ReportBlock, sources: dict[str, TranscriptSegment]) -> str:
    title = f'<h3>{escape(block.title)}</h3>' if block.title else ''
    detail = f'<details><summary>Details / 詳情</summary><p>{escape(block.detail)}</p></details>' if block.detail else ''
    evidence = ''.join(f'<blockquote>{escape(sources[key].text)}</blockquote>' for key in block.source_segment_ids if key in sources)
    source = f'<details><summary>Transcript sources / 逐字稿來源</summary>{evidence}</details>' if evidence else ''
    # Type is a validated enum, and escaped defensively for the class attribute.
    return f'<article class="card {escape(block.type, quote=True)}">{title}<p>{escape(block.text)}</p>{detail}{source}</article>'


def write_standalone_report(path: Path, report: ReportRevision, transcript: Sequence[TranscriptSegment]) -> None:
    doc = report.document
    sources = {segment.id: segment for segment in transcript}
    nav = ''.join(f'<a href="#part-{i}">{i:02d} · {escape(section.title)}</a>' for i, section in enumerate(doc.sections, 1))
    sections = []
    for i, section in enumerate(doc.sections, 1):
        cards = ''.join(_block(block, sources) for block in section.blocks)
        sections.append(f'<section id="part-{i}" data-section><div class="eyebrow">PART {i:02d}</div>'
                        f'<h2>{escape(section.title)}</h2><button type="button" data-copy="content-{i}">Copy section / 複製章節</button>'
                        f'<details open><summary>Show or hide section / 展開或收合章節</summary>'
                        f'<div id="content-{i}" class="grid {escape(section.layout_type, quote=True)}">{cards}</div></details></section>')
    takeaways = ''.join(_block(block, sources) for block in doc.takeaways)
    warnings = ''.join(f'<p class="warning">{escape(w)}</p>' for w in doc.warnings)
    html = f'''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
<title>{escape(doc.title or 'Meeting report')}</title><style>{_STYLE}</style></head><body><main id="report"><header id="top">
<div class="eyebrow">MEETING REPORT / 會議報告</div><h1>{escape(doc.title or 'Meeting report')}</h1><p>{escape(doc.executive_summary)}</p>
<p class="muted">Revision / 版本 {report.revision}</p>{warnings}<div class="controls"><button id="mode" type="button">Presentation / 簡報模式</button>
<button id="fullscreen" type="button" hidden>Full screen / 全螢幕</button><button type="button" data-copy="report-content">Copy report / 複製報告</button><span id="copy-status" role="status"></span></div>
<div class="grid">{takeaways}</div></header><nav id="toc" aria-label="Report contents / 報告目錄">{nav}</nav>
<div id="pager" class="controls pager" hidden><button id="prev" type="button">Previous / 上一頁</button><span id="counter"></span><button id="next" type="button">Next / 下一頁</button></div>
<div id="report-content">{''.join(sections)}</div><a class="top" href="#top">Back to top / 回到頂部</a></main><script>{_SCRIPT}</script></body></html>'''
    path.write_text(html, encoding='utf-8')
