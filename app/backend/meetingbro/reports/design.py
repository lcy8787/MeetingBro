"""Bounded Layout Composer and deterministic Design Review. Never emits executable UI."""
from __future__ import annotations

from ..report_contracts import CHART_TYPES, GRAPH_TYPES, MATRIX_TYPES, TABLE_TYPES, component_kind
from ..schemas import DesignFinding, LayoutNode, ReportDocument, ReportSection


def validate_layout(section: ReportSection) -> None:
    if section.layout is None:
        return
    placed: list[int] = []
    count = 0

    def walk(node: LayoutNode, depth: int, interactive: bool = False) -> None:
        nonlocal count
        count += 1
        if count > 40 or depth > 3:
            raise ValueError('Layout exceeds depth/node budget')
        if node.op == 'block':
            if node.children or node.block_index is None or node.block_index >= len(section.blocks):
                raise ValueError('Invalid layout block reference')
            if interactive and section.blocks[node.block_index].type in {'TabGroup', 'Accordion'}:
                raise ValueError('Nested interactive components are not allowed')
            placed.append(node.block_index)
        else:
            if node.block_index is not None or not node.children:
                raise ValueError('Layout containers need children, not a block reference')
            if node.op == 'split' and not 2 <= node.columns <= 4:
                raise ValueError('Split layout needs 2..4 columns')
            if node.op in {'tabs', 'accordion'} and interactive:
                raise ValueError('Nested interactive layouts are not allowed')
            for child in node.children:
                walk(child, depth + 1, interactive or node.op in {'tabs', 'accordion'})
    walk(section.layout, 0)
    if sorted(placed) != list(range(len(section.blocks))):
        raise ValueError('Layout must place every block exactly once')


def compose_layout(section: ReportSection) -> LayoutNode | None:
    """Safe semantic fallback; a valid agent tree takes precedence."""
    leaves = []
    dense = CHART_TYPES | GRAPH_TYPES | MATRIX_TYPES | TABLE_TYPES
    for index, block in enumerate(section.blocks):
        kind = component_kind(block.type)
        wide = (block.type in dense or kind in {'paragraph', 'quote', 'timeline', 'step', 'hero', 'heading', 'reference'}
                or len(block.text + block.detail) > 240 or bool(block.options))
        strong = kind in {'decision', 'conclusion', 'insight'}
        leaves.append(LayoutNode(op='block', block_index=index, span=12 if wide or strong else 6,
                                 emphasis='strong' if strong else 'quiet' if kind == 'reference' else 'normal'))
    if len(leaves) > 36:
        return None  # Long merged topics remain readable through the existing sequential renderer.
    if len(leaves) > 24:
        groups = [LayoutNode(op='grid', columns=2, children=leaves[i:i+12]) for i in range(0, len(leaves), 12)]
        return LayoutNode(op='stack', children=groups)
    return LayoutNode(op='grid', columns=2, children=leaves)


def review_design(doc: ReportDocument, *, repair: bool = False) -> list[DesignFinding]:
    findings = []

    def issue(section, code, message, repaired=False):
        findings.append(DesignFinding(section_id=section.id, code=code, message=message, repaired=repaired))

    for section in doc.sections:
        validate_layout(section)
        if section.layout is None:
            if repair:
                section.layout = compose_layout(section)
        if not section.blocks:
            continue
        kinds = [component_kind(b.type) for b in section.blocks]
        if len(kinds) >= 4 and len(set(kinds)) == 1 and kinds[0] in {'concept', 'statistic', 'insight'}:
            issue(section, 'repeated_cards', 'Repeated card treatment obscures the information hierarchy.')
        contents = [' '.join(b.text.split()).casefold() for b in section.blocks]
        if len(set(contents)) < len(contents):
            issue(section, 'repeated_content', 'Repeated text: check attribution before consolidating.')
        if sum(len(b.text) + len(b.detail) + sum(len(o.text) for o in b.options) for b in section.blocks) > 4200:
            issue(section, 'information_density', 'Dense section: consider coherent subtopics or expandable supporting detail.')
        layout = section.layout
        if layout is None:
            issue(section, 'layout_budget', 'Long topic uses sequential rendering to stay within the layout complexity limit.', repair)
            continue
        leaves = []
        max_depth = 0

        def walk(node, depth):
            nonlocal max_depth
            max_depth = max(max_depth, depth)
            if node.op == 'block':
                leaves.append(node)
            for child in node.children:
                walk(child, depth + 1)
        walk(layout, 0)
        if max_depth >= 3:
            issue(section, 'excessive_nesting', 'Flattened a deeply nested layout for readability.', repair)
            if repair:
                section.layout = compose_layout(section)
                leaves = section.layout.children
        for leaf in leaves:
            block = section.blocks[leaf.block_index]
            if leaf.span < 6 and (len(block.text + block.detail) > 180 or block.type in CHART_TYPES | GRAPH_TYPES | MATRIX_TYPES | TABLE_TYPES):
                issue(section, 'narrow_dense_block', 'Dense content needs at least half the available width.', repair)
                if repair:
                    leaf.span = 12
        strong = [leaf for leaf in leaves if leaf.emphasis == 'strong']
        if len(strong) > 2:
            issue(section, 'visual_hierarchy', 'Too many primary highlights compete for attention.', repair)
            if repair:
                for leaf in strong[2:]:
                    leaf.emphasis = 'normal'
        for block in section.blocks:
            if block.type in CHART_TYPES and len(block.data) < 2:
                issue(section, 'chart_suitability', 'A comparative chart needs at least two supported observations.')
    return findings
