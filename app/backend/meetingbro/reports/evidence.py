"""Additional evidence checks for visual encodings. Semantic entailment still needs human review."""
from decimal import Decimal, InvalidOperation
import re

from ..report_contracts import CHART_TYPES, GRAPH_TYPES, MATRIX_TYPES, component_kind, component_payload_fields
from .errors import ReportValidationError
from .quantities import number_tokens


def numeric(value: str) -> Decimal:
    # Never coerce words, NaN, infinity, exponents or ambiguous comma separators.
    if not re.fullmatch(r'[-+]?\d+(?:\.\d+)?', value) or len(value) > 24:
        raise ValueError('Chart value must be an exact finite numeric token')
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError('Invalid chart value') from exc
    if not result.is_finite() or abs(result) > Decimal('1e12') or (result and abs(result) < Decimal('1e-9')):
        raise ValueError('Chart value outside safe rendering range')
    return result


_PUNCTUATION = re.compile(r'[\s，。！？、；：,.!?;:\'"“”‘’（）()\[\]{}<>《》…—–-]+')


def _quote_key(text: str) -> str:
    return _PUNCTUATION.sub('', text)


def exact_quote(quote, ids, sources):
    if not quote.strip() or not ids or any(key not in sources for key in ids):
        raise ReportValidationError('Missing exact transcript evidence', code='invalid_report_evidence')
    if any(quote in sources[key] for key in ids):
        return
    key = _quote_key(quote)
    if key and any(key in _quote_key(sources[segment_id]) for segment_id in ids):
        return
    # ASR often splits one spoken sentence across adjacent segments. A model
    # may cite both segments and join them with or without punctuation.
    joined = _quote_key(''.join(sources[key] for key in ids))
    if key and key in joined:
        return
    raise ReportValidationError('Evidence quote must occur verbatim in a cited segment', code='invalid_report_evidence')


def anchored_quote_ids(quote, ids, sources, speakers):
    """Extend a known citation only when its exact excerpt crosses an ASR boundary."""
    try:
        exact_quote(quote, ids, sources)
        return ids
    except ReportValidationError:
        if len(ids) != 1 or ids[0] not in sources:
            raise
        anchor = ids[0]
        ordered = list(sources)
        position = ordered.index(anchor)
        needle = _quote_key(quote)
        for other in (position - 1, position + 1):
            if not 0 <= other < len(ordered) or speakers[ordered[other]] != speakers[anchor]:
                continue
            pair = [ordered[n] for n in sorted([other, position])]
            left, right = (_quote_key(sources[k]) for k in pair)
            start = (left + right).find(needle)
            if start >= 0 and start < len(left) < start + len(needle):
                return pair
        raise


_CJK = re.compile(r'[\u3400-\u9fff]')


def _cross_script(term: str, quote: str) -> bool:
    """Allow translated literal labels when evidence stays in the source language.

    Exact quotes, numeric values and units remain checked; this only avoids
    rejecting a Chinese summary label merely because the transcript quote is English.
    """
    return bool(_CJK.search(term)) != bool(_CJK.search(quote))


def _term_supported(term: str, text: str) -> bool:
    """Accept a concise label that overlaps its cited text, not only an exact copy."""
    if not term:
        return True
    if term in text:
        return True
    for run in re.findall(r'[\u3400-\u9fff]+', term):
        for index in range(len(run) - 1):
            if run[index:index + 2] in text:
                return True
    for word in re.findall(r'[A-Za-z]{4,}', term):
        if word.casefold() in text.casefold():
            return True
    return False


def literal(term, quote):
    if term and term not in quote and not _cross_script(term, quote) and not _term_supported(term, quote):
        raise ReportValidationError('Visual category or unit absent from exact evidence', code='invalid_report_evidence')


def value_in_quote(value, unit, quote):
    number = r'(?<![\d.])' + re.escape(value) + r'(?!\d|\.\d)'
    if re.search(number + r'\s*' + re.escape(unit), quote) or (unit and re.search(re.escape(unit) + r'\s*' + number, quote)):
        return True
    try:
        target = Decimal(value)
    except InvalidOperation:
        return False
    if unit and unit not in quote and not _cross_script(unit, quote):
        return False
    arabic, chinese, spoken = number_tokens(quote)
    return any(parsed == target for _, parsed in arabic | chinese | spoken)


def validate_component_fields(block):
    allowed = component_payload_fields(block.type)
    for field in ('options', 'columns', 'rows', 'data', 'nodes', 'edges', 'cells'):
        if getattr(block, field) and field not in allowed:
            raise ReportValidationError('This component contains an incompatible data field.',
                code='invalid_report_component_data', location=field)


def validate_visual(block, sources, validate_text):
    validate_component_fields(block)
    def sourced(item, text):
        validate_text(text, item.source_segment_ids, sources)
        if not set(item.source_segment_ids) <= set(block.source_segment_ids):
            raise ReportValidationError('Visual item sources must be included in parent block sources', code='invalid_report_sources')

    if block.evidence_quote:
        exact_quote(block.evidence_quote, block.source_segment_ids, sources)
    if block.type in {'DecisionPanel', 'ActionItems'}:
        if block.evidence_quote:
            exact_quote(block.evidence_quote, block.source_segment_ids, sources)
        elif not block.options:
            # A single decision/task statement still needs verbatim support.
            exact_quote(block.text, block.source_segment_ids, sources)
        # Container action/decision lists are grounded through each option's
        # own citations; a redundant block-level quote is not required.
    if component_kind(block.type) == 'quote':
        exact_quote(block.text, block.source_segment_ids, sources)
    if block.type == 'SpeakerQuote' and (not block.speaker_id or block.evidence_quote != block.text):
        raise ValueError('Speaker quote needs identified speaker and exact wording')

    if block.type in CHART_TYPES:
        if len(block.data) < 2 or block.options:
            raise ValueError('Chart needs comparable sourced observations')
        if len({d.unit for d in block.data}) != 1:
            raise ValueError('Chart units are not comparable')
        keys = [(d.series, d.label) for d in block.data]
        if len(set(keys)) != len(keys):
            raise ValueError('Duplicate chart observation')
        by_series = {}
        for datum in block.data:
            sourced(datum, ' '.join([datum.label, datum.series, datum.evidence_quote]))
            numeric(datum.value)
            exact_quote(datum.evidence_quote, datum.source_segment_ids, sources)
            literal(datum.label, datum.evidence_quote)
            literal(datum.unit, datum.evidence_quote)
            # Series names group several observations and need not repeat in
            # every quote; they must still be supported by the block or quote.
            if not _term_supported(datum.series, ' '.join([block.title, block.text, datum.evidence_quote])):
                raise ReportValidationError('Chart series absent from its context', code='invalid_report_evidence')
            if not datum.label.strip() or not value_in_quote(datum.value, datum.unit, datum.evidence_quote):
                raise ReportValidationError('Value/unit pair is absent from the quoted observation', code='invalid_report_evidence')
            # A chart point must not borrow another observation's value from a large quoted passage.
            # Numbers within literal category labels (e.g. a year) remain legitimate context.
            tokens = lambda text: set(re.findall(r'(?<![A-Za-z0-9_])[-+]?\d+(?:\.\d+)?', text))
            allowed = tokens(' '.join([datum.label, datum.series, datum.unit, datum.value]))
            if not tokens(datum.evidence_quote) <= allowed:
                raise ReportValidationError('Quote contains multiple numeric observations; quote only this data point', code='invalid_report_evidence')
            by_series.setdefault(datum.series, []).append(datum)
        if block.type == 'LineChart':
            exact_quote(block.evidence_quote, block.source_segment_ids, sources)
            for values in by_series.values():
                if len(values) < 3:
                    raise ValueError('Line chart needs three ordered observations per series')
                positions = [block.evidence_quote.find(d.label) for d in values]
                if any(p < 0 for p in positions) or positions != sorted(set(positions)):
                    raise ReportValidationError('Line observations must follow the quoted sequence', code='invalid_report_evidence')
    elif block.data:
        raise ValueError('Only numeric chart components may contain data')

    if block.type in GRAPH_TYPES:
        nodes = {node.id: node for node in block.nodes}
        if len(nodes) != len(block.nodes) or len(nodes) < 2 or not block.edges:
            raise ValueError('Graph needs unique nodes and explicit relationships')
        for node in block.nodes:
            if not node.id.strip() or not node.label.strip():
                raise ValueError('Empty graph node')
            sourced(node, node.label + ' ' + node.text)
            node_sources = ' '.join(sources[key] for key in node.source_segment_ids)
            if not _term_supported(node.label, node_sources) and not _cross_script(node.label, node_sources):
                raise ReportValidationError('Graph labels must be transcript terms', code='invalid_report_evidence')
        seen = set()
        parents = {}
        for edge in block.edges:
            if edge.source not in nodes or edge.target not in nodes or edge.source == edge.target:
                raise ValueError('Invalid graph endpoint')
            key = (edge.source, edge.target, edge.relation, edge.label)
            if key in seen:
                raise ValueError('Duplicate graph edge')
            seen.add(key)
            sourced(edge, edge.label + ' ' + edge.evidence_quote)
            exact_quote(edge.evidence_quote, edge.source_segment_ids, sources)
            edge_sources = ' '.join(sources[key] for key in edge.source_segment_ids)
            for term in [nodes[edge.source].label, nodes[edge.target].label]:
                if not (term in edge.evidence_quote or _cross_script(term, edge.evidence_quote)
                        or _term_supported(term, edge_sources)):
                    raise ReportValidationError('Graph endpoints are absent from evidence', code='invalid_report_evidence')
            if not _term_supported(edge.label, edge_sources) and not _cross_script(edge.label, edge_sources):
                raise ReportValidationError('Graph edge labels must be transcript terms', code='invalid_report_evidence')
            if edge.relation != 'related':
                left = edge.evidence_quote.find(nodes[edge.source].label)
                right = edge.evidence_quote.find(nodes[edge.target].label)
                reverse = edge.relation == 'causes' and edge.label.casefold() in {'because', 'because of', 'wegen', '因為', '因为', '由於', '由于', '源於', '源于'}
                if left >= 0 and right >= 0 and (left < right) == reverse:
                    raise ReportValidationError('Directed endpoints contradict the quoted relationship order', code='invalid_report_evidence')
            allowed = {'CausalFlow': {'causes'}, 'ArgumentMap': {'supports', 'opposes'},
                       'DecisionTree': {'branch'}, 'SankeyDiagram': {'flow'}}.get(block.type)
            if allowed and edge.relation not in allowed:
                raise ValueError('Relationship does not match graph semantics')
            if block.type == 'SankeyDiagram':
                if numeric(edge.value) <= 0 or not edge.unit:
                    raise ValueError('Sankey flow needs positive value and explicit unit')
                if not value_in_quote(edge.value, edge.unit, edge.evidence_quote):
                    raise ReportValidationError('Sankey value/unit pair absent from evidence', code='invalid_report_evidence')
            elif edge.value or edge.unit:
                raise ValueError('Only Sankey edges may encode weights')
            parents.setdefault(edge.target, []).append(edge.source)
        if block.type in {'DecisionTree', 'SankeyDiagram'}:
            if block.type == 'SankeyDiagram' and len({e.unit for e in block.edges}) != 1:
                raise ValueError('Sankey flows need one unit')
            if block.type == 'DecisionTree' and any(len(p) > 1 for p in parents.values()):
                raise ValueError('Decision tree node cannot have multiple parents')
            visited, active = set(), set()
            def visit(key):
                if key in active:
                    raise ValueError('Tree/flow must be acyclic')
                if key in visited:
                    return
                active.add(key)
                for edge in block.edges:
                    if edge.source == key:
                        visit(edge.target)
                active.remove(key)
                visited.add(key)
            for key in nodes:
                visit(key)
            if block.type == 'SankeyDiagram':
                for key in nodes:
                    incoming = [numeric(edge.value) for edge in block.edges if edge.target == key]
                    outgoing = [numeric(edge.value) for edge in block.edges if edge.source == key]
                    if incoming and outgoing and sum(incoming) != sum(outgoing):
                        raise ValueError('Unbalanced stated flows: use a table instead of implying a missing allocation')
            if block.type == 'DecisionTree':
                roots = set(nodes) - set(parents)
                if len(roots) != 1:
                    raise ValueError('Decision tree needs one connected root')
    elif block.nodes or block.edges:
        raise ValueError('Only graph components may contain nodes/edges')

    if block.type in MATRIX_TYPES:
        if len(block.cells) < 2 or len({(c.row, c.column) for c in block.cells}) != len(block.cells):
            raise ValueError('Matrix needs unique sourced cells')
        if block.type == 'Heatmap' and len({c.unit for c in block.cells}) != 1:
            raise ValueError('Heatmap cells need one comparable unit')
        for cell in block.cells:
            sourced(cell, ' '.join([cell.row, cell.column, cell.evidence_quote]))
            exact_quote(cell.evidence_quote, cell.source_segment_ids, sources)
            if not all(term.strip() for term in [cell.row, cell.column, cell.value]):
                raise ValueError('Matrix needs nonempty categories/value')
            for term in [cell.row, cell.column, cell.value, cell.unit]:
                literal(term, cell.evidence_quote)
            if block.type == 'Heatmap':
                numeric(cell.value)
    elif block.cells:
        raise ValueError('Only matrix components may contain cells')
