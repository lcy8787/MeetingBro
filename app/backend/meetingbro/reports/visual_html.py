"""Trusted, escaped SVG/HTML visual renderers. Models never supply markup or geometry."""
from html import escape as e
from .sankey import sankey_geometry
from math import cos, sin, pi, hypot, ceil


def visual_text(block):
    nodes = {node.id: node.label for node in block.nodes}
    return [*(f'{d.series} {d.label}: {d.value} {d.unit}' for d in block.data),
            *(f'{n.label}: {n.text}' for n in block.nodes),
            *(f'{nodes.get(edge.source, edge.source)} → {nodes.get(edge.target, edge.target)}: '
              f'{edge.label} {edge.value} {edge.unit}\n{edge.evidence_quote}' for edge in block.edges),
            *(f'{c.row} | {c.column}: {c.value} {c.unit}' for c in block.cells)]


def _number(value):
    """Only validated bounded numeric values reach normal exports; fail closed on corrupt legacy data."""
    from .evidence import numeric
    return float(numeric(value))


def chart(block, sources, evidence):
    data = block.data
    labels = list(dict.fromkeys(d.label for d in data))
    groups = list(dict.fromkeys(d.series for d in data))
    low, high = min(0, *(_number(d.value) for d in data)), max(0, *(_number(d.value) for d in data))
    span = high - low or 1
    y = lambda value: 250 - (value - low) / span * 205
    x = lambda label: 70 + (labels.index(label) + .5) * 610 / max(1, len(labels))
    width = min(64, 610 / max(1, len(labels)) * .7 / max(1, len(groups)))
    line = block.type == 'LineChart'
    marks = []
    for group_index, group in enumerate(groups):
        if line:
            points = ' '.join(f'{x(d.label):.3f},{y(_number(d.value)):.3f}' for d in data if d.series == group)
            marks.append(f'<polyline data-series="{group_index}" class="report-series-{group_index % 4}" fill="none" stroke-width="2" points="{points}"/>')
    for index, d in enumerate(data):
        group = groups.index(d.series)
        cx = x(d.label) + (0 if line else (group - (len(groups) - 1) / 2) * width)
        title = e(f'{d.series} {d.label}: {d.value} {d.unit}', quote=True)
        attrs = f'class="report-series-{group % 4}" data-series="{group}" data-observation="{index}" tabindex="0" role="button" aria-label="{title}"'
        if line:
            mark = f'<circle {attrs} cx="{cx:.3f}" cy="{y(_number(d.value)):.3f}" r="6"><title>{title}</title></circle>'
        else:
            mark = f'<rect {attrs} x="{cx - width / 2:.3f}" y="{min(y(0), y(_number(d.value))):.3f}" width="{width * .9:.3f}" height="{max(2, abs(y(_number(d.value)) - y(0))):.3f}" rx="3"><title>{title}</title></rect>'
        marks.append(mark)
    ticks = ''.join(f'<line x1="55" x2="690" y1="{y(v):.3f}" y2="{y(v):.3f}" class="report-chart-grid"/><text x="48" y="{y(v) + 4:.3f}" text-anchor="end">{v:g}</text>' for v in [low, high])
    ticks += f'<line x1="55" x2="690" y1="{y(0):.3f}" y2="{y(0):.3f}" class="report-chart-axis"/>'
    axis = ''.join(f'<text x="{x(label):.3f}" y="278" text-anchor="middle"><title>{e(label)}</title>{e(label[:11] + "…" if len(label) > 12 else label)}</text>' for label in labels)
    buttons = ('<button type="button" data-series-select="-1" aria-pressed="true">All / 全部</button>' + ''.join(f'<button type="button" data-series-select="{i}" class="report-series-key report-series-{i % 4}" aria-pressed="false">{e(group or "Values / 數值")}</button>' for i, group in enumerate(groups))) if len(groups) > 1 else ''
    rows = ''.join(f'<tr><td>{e(d.series)}</td><th scope="row">{e(d.label)}</th><td>{e(d.value)} {e(d.unit)}{evidence(d.source_segment_ids, sources)}</td></tr>' for d in data)
    selections = ''.join(f'<div data-observation-panel="{i}" hidden><strong>{e(d.series)} {e(d.label)}: {e(d.value)} {e(d.unit)}</strong><p>{e(d.evidence_quote)}</p>{evidence(d.source_segment_ids, sources)}</div>' for i, d in enumerate(data))
    return f'<div class="report-visual" data-visual><div class="choices report-choice-buttons" role="group" aria-label="Series / 數據系列">{buttons}</div><div class="report-svg-scroll" role="region" tabindex="0" aria-label="{e(block.title or block.type, quote=True)}"><svg viewBox="0 0 720 310" role="group" aria-label="{e(block.title or block.type, quote=True)}"><title>{e(block.title)} · {e(data[0].unit)} · zero baseline / 零基線</title>{ticks}{"".join(marks)}{axis}</svg></div><p class="report-chart-caption">{e(data[0].unit)} · Select a mark to inspect its source / 選取圖形查看來源</p><div class="report-selected" role="status">{selections}</div><details><summary>Data and sources / 數據及來源</summary><div class="table-scroll"><table><thead><tr><th>Series / 系列</th><th>Observation / 項目</th><th>Value / 數值</th></tr></thead><tbody>{rows}</tbody></table></div></details></div>'


def graph(block, sources, evidence, uid):
    nodes, edges = block.nodes, block.edges
    levels = {n.id: 0 for n in nodes}
    layered = block.type in {'DecisionTree', 'SankeyDiagram'}
    if layered:
        for _ in nodes:
            for edge in edges:
                levels[edge.target] = max(levels[edge.target], levels[edge.source] + 1)
    max_level = max(1, *levels.values())
    sankey = sankey_geometry(block) if block.type == 'SankeyDiagram' else None
    height = sankey['height'] if sankey else max(320, ceil(len(nodes) / 3) * 110)
    def position(key):
        index = next(i for i, n in enumerate(nodes) if n.id == key)
        if layered:
            peers = [n for n in nodes if levels[n.id] == levels[key]]
            return 90 + levels[key] / max_level * 540, (next(i for i, n in enumerate(peers) if n.id == key) + 1) / (len(peers) + 1) * height
        angle = -pi / 2 + index / len(nodes) * 2 * pi
        return 360 + cos(angle) * 255, height / 2 + sin(angle) * (height / 2 - 55)
    links, boxes = [], []
    for edge in ([] if sankey else edges):
        x1, y1 = position(edge.source); x2, y2 = position(edge.target)
        dx, dy = x2 - x1, y2 - y1
        length = hypot(dx, dy) or 1
        clearance = min(70 * length / abs(dx) if dx else float('inf'), 23 * length / abs(dy) if dy else float('inf')) + 6
        width = 2
        marker = f' marker-end="url(#{uid})"' if edge.relation != 'related' else ''
        label = f'{edge.value} {edge.unit}' if edge.value else edge.label[:18]
        links.append(f'<g class="report-edge report-edge--{e(edge.relation)}"><line x1="{x1 + dx / length * clearance:.3f}" y1="{y1 + dy / length * clearance:.3f}" x2="{x2 - dx / length * clearance:.3f}" y2="{y2 - dy / length * clearance:.3f}" stroke-width="{width:.3f}"{marker}/><text x="{(x1+x2)/2:.3f}" y="{(y1+y2)/2-7:.3f}" text-anchor="middle">{e(label)}</text></g>')
    if sankey:
        for index, ribbon in enumerate(sankey['ribbons']):
            edge = edges[index]
            links.append(f'<g class="report-sankey-ribbon report-series-{index % 4}"><path d="{ribbon["path"]}"><title>{e(edge.value)} {e(edge.unit)} · {e(edge.evidence_quote)}</title></path><text x="{ribbon["label_x"]:.3f}" y="{ribbon["label_y"]:.3f}" text-anchor="middle">{e(edge.value)} {e(edge.unit)}</text></g>')
    for index, node in enumerate(nodes):
        label = node.label[:12] + '…' if len(node.label) > 13 else node.label
        if sankey:
            box = sankey['nodes'][node.id]
            x, y = box['x'], box['y']
            right = x > 580
            anchor = 'end' if right else 'start'
            shape = f'<rect x="{x:.3f}" y="{y:.3f}" width="14" height="{max(2, box["height"]):.3f}" rx="2"/><text x="{x-8 if right else x+22:.3f}" y="{y+box["height"]/2+5:.3f}" text-anchor="{anchor}">{e(label)}</text>'
        else:
            x, y = position(node.id)
            shape = f'<rect x="{x-70:.3f}" y="{y-23:.3f}" width="140" height="46" rx="8"/><text x="{x:.3f}" y="{y+5:.3f}" text-anchor="middle">{e(label)}</text>'
        boxes.append(f'<g class="report-graph-node" data-observation="{index}" tabindex="0" role="button" aria-label="{e(node.label, quote=True)}"><title>{e(node.label)}: {e(node.text)}</title>{shape}</g>')
    selections = ''.join(f'<div data-observation-panel="{i}" hidden><strong>{e(n.label)}</strong><p>{e(n.text)}</p>{evidence(n.source_segment_ids, sources)}</div>' for i, n in enumerate(nodes))
    relationships = ''.join(f'<div class="report-relationship"><strong>{e(next(n.label for n in nodes if n.id == edge.source))} → {e(next(n.label for n in nodes if n.id == edge.target))}</strong><p>{e(edge.label)} {e(edge.value)} {e(edge.unit)}</p><blockquote>{e(edge.evidence_quote)}</blockquote>{evidence(edge.source_segment_ids, sources)}</div>' for edge in edges)
    node_details = ''.join(f'<div><strong>{e(n.label)}</strong><p>{e(n.text)}</p>{evidence(n.source_segment_ids, sources)}</div>' for n in nodes)
    return f'<div class="report-visual" data-visual><div class="report-svg-scroll" role="region" tabindex="0" aria-label="{e(block.title or block.type, quote=True)}"><svg viewBox="0 0 720 {height}" role="group" aria-label="{e(block.title or block.type, quote=True)}"><title>{e(block.title or block.type)}</title><defs><marker id="{uid}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor"/></marker></defs>{"".join(links)}{"".join(boxes)}</svg></div><div class="report-selected" role="status">{selections}</div><details><summary>Relationships and sources / 關係及來源</summary>{node_details}{relationships}</details></div>'


def matrix(block, sources, evidence):
    rows = list(dict.fromkeys(c.row for c in block.cells))
    columns = list(dict.fromkeys(c.column for c in block.cells))
    heatmap = block.type == 'Heatmap'
    values = [_number(c.value) for c in block.cells] if heatmap else []
    low, high = (min(values), max(values)) if values else (0, 0)
    rendered = []
    for row in rows:
        cells = []
        for col in columns:
            cell = next((c for c in block.cells if c.row == row and c.column == col), None)
            color = f' style="background-color:rgba(76,201,170,{.08 + (_number(cell.value)-low)/(high-low or 1)*.3:.3f})"' if heatmap and cell else ''
            cells.append(f'<td{color}>{e(cell.value)} {e(cell.unit)}{evidence(cell.source_segment_ids, sources)}</td>' if cell else '<td aria-label="Not stated / 未提及">—</td>')
        rendered.append(f'<tr><th scope="row">{e(row)}</th>{"".join(cells)}</tr>')
    caption = f'<p class="report-chart-caption">{low:g} → {high:g} {e(block.cells[0].unit)} · Dark → bright / 深 → 淺；— = 未提及</p>' if heatmap else ''
    headings = ''.join(f'<th scope="col">{e(col)}</th>' for col in columns)
    return f'<div class="table-scroll" role="region" aria-label="{e(block.title or block.type, quote=True)}" tabindex="0">{caption}<table><thead><tr><th scope="col">{e(block.title)}</th>{headings}</tr></thead><tbody>{"".join(rendered)}</tbody></table></div>'
