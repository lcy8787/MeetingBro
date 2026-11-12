"""End-to-end contracts for source-grounded generative UI and bounded design tools."""
import copy
import json
from datetime import datetime, timezone

import pytest

from meetingbro.reports.components import COMPONENTS, REPORT_SCHEMA
from meetingbro.reports.design import review_design, validate_layout
from meetingbro.reports.generator import ReportGenerationError, generate_report
from meetingbro.reports.standalone import write_standalone_report
from meetingbro.schemas import LayoutNode, ReportDocument, ReportRevision, ReportSection, TranscriptSegment
from meetingbro.storage.db import Storage


def transcript():
    text = ('Alice: January cost 12 USD. February cost 6 USD. March cost 9 USD. '
            'Delay causes Risk. Evidence supports Plan. Plan opposes Risk. '
            'Budget to Delivery flows 12 USD. Budget to Research flows 6 USD. '
            'If Ready then Launch. If Ready then Wait. '
            'Delivery likelihood high impact high. Research likelihood low impact high. '
            'Team January 12 USD. Team February 6 USD. '
            'We agree to Launch. Alice will review Plan.')
    return [TranscriptSegment(id='s', meeting_id='m', text=text, speaker_id='Alice', start_time=0,
        end_time=60, original_language='en', confidence=1, created_at=datetime.now(timezone.utc))]


def block(kind='EditorialText', **kwargs):
    return dict(type=kind, title='Plan', text='Plan', source_segment_ids=['s']) | kwargs


def doc(*blocks, layout=None):
    return dict(title='Planning', executive_summary='', executive_summary_source_segment_ids=[], sections=[
        dict(id='planning', title='Planning', layout_type='mixed', blocks=list(blocks or [block()]), layout=layout)])


class Client:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []
    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return json.dumps(next(self.responses))


def generate(payload, *more):
    return generate_report(transcript(), language='en', client=Client(payload, *more))


def chart(kind='BarChart'):
    return block(kind, data=[dict(label=month, value=value, unit='USD', series='cost',
        evidence_quote=f'{month} cost {value} USD.', source_segment_ids=['s'])
        for month, value in [('January', '12'), ('February', '6'), ('March', '9')]],
        evidence_quote='January cost 12 USD. February cost 6 USD. March cost 9 USD.')


def graph(kind='CausalFlow'):
    return block(kind, nodes=[dict(id=key, label=label, source_segment_ids=['s']) for key, label in [('a', 'Delay'), ('b', 'Risk')]],
        edges=[dict(source='a', target='b', label='causes', relation='causes', evidence_quote='Delay causes Risk.', source_segment_ids=['s'])])


def tool(name, payload=None, index=0, query=''):
    return {**(payload or dict(title='', executive_summary='', executive_summary_source_segment_ids=[], sections=[])),
            'tool_request': dict(name=name, query=query, section_index=index)}


def revision(document):
    return ReportRevision(report_id='r', meeting_id='m', report_type='meeting', revision=1,
        document=document, created_at=datetime.now(timezone.utc))


def test_registry_covers_all_requested_components_and_strict_plain_data():
    names = 'HeroSection SectionHeader EditorialText SummaryCard InsightCallout ConclusionPanel ComparisonCards ComparisonMatrix ProsCons PerspectiveCards SpecificationTable Timeline Stepper ProcessFlow CausalFlow KeyMetrics DataBars BarChart LineChart DataTable DecisionPanel ActionItems OpenQuestions SpeakerQuote TranscriptReference Accordion TabGroup ExpandableDetails KnowledgeGraph ArgumentMap DecisionTree RiskMatrix Heatmap SankeyDiagram InteractiveChart'.split()
    assert set(names) <= COMPONENTS.keys()
    schema = REPORT_SCHEMA['properties']['sections']['items']['properties']['blocks']['items']
    assert schema['additionalProperties'] is False
    assert 'title' in schema['properties'] and 'title' in schema['required']
    assert set(names) <= set(schema['properties']['type']['enum'])


def test_design_tools_query_compose_review_revise_then_publish():
    initial = doc(block('SummaryCard'), chart())
    replacement = doc(block('ConclusionPanel', text='We agree to Launch.'))
    revised = copy.deepcopy(initial)
    revised['sections'][0] = replacement['sections'][0]
    client = Client(tool('list_components', query='chart'), tool('compose_layout', initial), tool('review_design'),
                    tool('revise_section', replacement), revised)
    result = generate_report(transcript(), language='en', client=client)
    assert len(client.calls) == 5
    contexts = [json.loads(c['user']) for c in client.calls]
    assert any(item['name'] == 'BarChart' and item['payload'] == 'data' for item in contexts[1]['tool_result']['result'])
    assert contexts[2]['tool_result']['result']['candidate']['sections'][0]['layout']['op'] == 'grid'
    assert contexts[4]['tool_result']['result']['valid']
    assert result.sections[0].blocks[0].type == 'ConclusionPanel'
    assert result.sections[0].layout.children[0].emphasis == 'strong'


def test_invalid_tool_candidate_returns_feedback_without_committing_then_allows_correction():
    invalid = doc(chart()); invalid['sections'][0]['blocks'][0]['data'][0]['value'] = '99'
    client = Client(tool('validate_structure', invalid), tool('revise_section', doc()), doc())
    result = generate_report(transcript(), language='en', client=client)
    assert result.sections
    assert not json.loads(client.calls[1]['user'])['tool_result']['result']['valid']
    assert 'existing candidate' in json.loads(client.calls[2]['user'])['tool_result']['result']['error']


def test_tool_loop_is_bounded_and_unknown_tools_fail_closed():
    client = Client(*(tool('list_components') for _ in range(5)))
    with pytest.raises(ReportGenerationError): generate_report(transcript(), language='en', client=client)
    assert len(client.calls) == 5
    with pytest.raises(ReportGenerationError): generate(tool('execute_javascript'))


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'unknown_op', 'extra_css', 'deep', 'nested_interaction'])
def test_dsl_rejects_missing_duplicate_code_and_excessive_structure(mutation):
    leaf = dict(op='block', block_index=0)
    layout = dict(op='grid', children=[leaf])
    if mutation == 'missing': layout['children'] = []
    if mutation == 'duplicate': layout['children'].append(copy.deepcopy(leaf))
    if mutation == 'unknown_op': layout['op'] = 'eval'
    if mutation == 'extra_css': layout['style'] = 'background:url(https://attacker)'
    if mutation == 'deep':
        for _ in range(4): layout = dict(op='stack', children=[layout])
    if mutation == 'nested_interaction': layout = dict(op='tabs', children=[dict(op='accordion', children=[leaf])])
    with pytest.raises(ReportGenerationError): generate(doc(block(), layout=layout))


def test_composer_honors_agent_order_width_and_composite_layout():
    layout = dict(op='grid', children=[dict(op='block', block_index=1, span=8),
        dict(op='stack', span=4, children=[dict(op='block', block_index=0)])])
    result = generate(doc(block('SummaryCard'), block('EditorialText'), layout=layout))
    assert result.sections[0].layout.children[0].block_index == 1
    assert result.sections[0].layout.children[1].span == 4
    validate_layout(result.sections[0])


def test_review_repairs_density_width_nesting_and_highlight_competition():
    blocks = [block('SummaryCard', text='Plan ' * 40) for _ in range(4)]
    layout = dict(op='grid', children=[dict(op='block', block_index=i, span=3, emphasis='strong') for i in range(4)])
    report = ReportDocument(**doc(*blocks, layout=layout))
    findings = review_design(report, repair=True)
    assert {'repeated_cards', 'narrow_dense_block', 'visual_hierarchy'} <= {f.code for f in findings}
    assert all(n.span == 12 for n in report.sections[0].layout.children)
    assert sum(n.emphasis == 'strong' for n in report.sections[0].layout.children) == 2
    nested = LayoutNode(op='stack', children=[LayoutNode(op='stack', children=[LayoutNode(op='stack', children=[LayoutNode(op='block', block_index=0)])])])
    report = ReportDocument(**doc(block(), layout=nested.model_dump()))
    assert review_design(report, repair=True)[0].code == 'excessive_nesting'
    assert report.sections[0].layout.op == 'grid'


@pytest.mark.parametrize('kind', ['DataBars', 'BarChart', 'LineChart', 'InteractiveChart'])
def test_grounded_numeric_charts_accept_exact_values_and_units(kind):
    result = generate(doc(chart(kind)))
    assert [d.value for d in result.sections[0].blocks[0].data] == ['12', '6', '9']


@pytest.mark.parametrize('mutation', ['number', 'unit', 'label', 'source', 'nonliteral', 'duplicate', 'nan', 'empty', 'order'])
def test_chart_rejects_invented_numbers_units_categories_sources_and_order(mutation):
    candidate = chart('LineChart')
    datum = candidate['data'][0]
    if mutation == 'number': datum['value'] = '99'
    if mutation == 'unit': datum['unit'] = 'EUR'
    if mutation == 'label': datum['label'] = 'April'
    if mutation == 'source': datum['source_segment_ids'] = ['foreign']
    if mutation == 'nonliteral': datum['evidence_quote'] = 'January 12 USD'
    if mutation == 'duplicate': candidate['data'].append(copy.deepcopy(datum))
    if mutation == 'nan': datum['value'] = 'NaN'
    if mutation == 'empty': candidate['data'] = []
    if mutation == 'order': candidate['data'].reverse()
    with pytest.raises(ReportGenerationError): generate(doc(candidate))


def test_causal_edges_require_explicit_source_quote_endpoints_and_relation_type():
    assert generate(doc(graph())).sections[0].blocks[0].edges[0].relation == 'causes'
    for field, value in [('target', 'missing'), ('relation', 'related'), ('label', 'invented'),
                         ('evidence_quote', 'Risk causes Delay.'), ('source_segment_ids', ['foreign'])]:
        candidate = graph(); candidate['edges'][0][field] = value
        with pytest.raises(ReportGenerationError): generate(doc(candidate))


def test_sankey_and_tree_reject_invented_weights_cycles_or_disconnected_roots():
    sankey = block('SankeyDiagram', nodes=[dict(id=k, label=v, source_segment_ids=['s']) for k, v in [('a','Budget'),('b','Delivery'),('c','Research')]],
        edges=[dict(source='a', target=key, label='flows', relation='flow', value=value, unit='USD', evidence_quote=f'Budget to {label} flows {value} USD.', source_segment_ids=['s']) for key,label,value in [('b','Delivery','12'),('c','Research','6')]])
    assert generate(doc(sankey)).sections[0].blocks[0].edges[0].value == '12'
    bad = copy.deepcopy(sankey); bad['edges'][0]['value'] = '6'
    with pytest.raises(ReportGenerationError): generate(doc(bad))
    tree = block('DecisionTree', nodes=[dict(id=k, label=v, source_segment_ids=['s']) for k,v in [('a','Ready'),('b','Launch'),('c','Wait')]],
        edges=[dict(source='a',target=k,label='then',relation='branch',evidence_quote=f'If Ready then {label}.',source_segment_ids=['s']) for k,label in [('b','Launch'),('c','Wait')]])
    assert generate(doc(tree)).sections[0].blocks[0].type == 'DecisionTree'
    tree['edges'].pop()
    with pytest.raises(ReportGenerationError): generate(doc(tree))


def test_risk_categories_and_heatmap_values_are_literal_and_missing_cells_are_unknown(tmp_path):
    risk = block('RiskMatrix', cells=[dict(row=row,column='impact high',value=value,evidence_quote=f'{row} likelihood {value} impact high.',source_segment_ids=['s']) for row,value in [('Delivery','high'),('Research','low')]])
    assert generate(doc(risk)).sections[0].blocks[0].cells[0].value == 'high'
    risk['cells'][0]['value'] = 'critical'
    with pytest.raises(ReportGenerationError): generate(doc(risk))
    heatmap = block('Heatmap', cells=[dict(row='Team',column=month,value=value,unit='USD',evidence_quote=f'Team {month} {value} USD.',source_segment_ids=['s']) for month,value in [('January','12'),('February','6')]])
    assert generate(doc(heatmap)).sections[0].blocks[0].cells[1].value == '6'


def test_quotes_decisions_actions_and_speaker_metadata_are_not_invented():
    for kind, text in [('SpeakerQuote','We agree to Launch.'), ('DecisionPanel','We agree to Launch.'), ('ActionItems','Alice will review Plan.')]:
        candidate = block(kind, text=text, evidence_quote=text, speaker_id='Alice' if kind == 'SpeakerQuote' else '')
        assert generate(doc(candidate)).sections[0].blocks[0].text == text
        candidate['evidence_quote'] = 'We agree to Wait.'
        with pytest.raises(ReportGenerationError): generate(doc(candidate))
    candidate = block('SpeakerQuote', text='We agree to Launch.', evidence_quote='We agree to Launch.',speaker_id='Bob')
    with pytest.raises(ReportGenerationError): generate(doc(candidate))


@pytest.mark.parametrize('field', ['data', 'nodes', 'edges', 'cells'])
@pytest.mark.parametrize('final_job', [False, True])
def test_all_visual_sources_stay_meeting_local_at_both_storage_boundaries(tmp_path, field, final_job):
    payload = chart() if field == 'data' else graph() if field in {'nodes','edges'} else block('Heatmap', cells=[dict(row='Team',column='January',value='12',source_segment_ids=['s'])])
    payload[field][0]['source_segment_ids'] = ['foreign']
    report = revision(ReportDocument(**doc(payload), generation_method='llm'))
    storage = Storage(tmp_path / 'report.sqlite')
    storage.create_meeting('m')
    storage.insert_segment(transcript()[0])
    try:
        with pytest.raises(ValueError):
            if final_job:
                job = storage.queue_final_summary('j','m','en',None); storage.claim_final_summary_job()
                storage.complete_final_summary_job(job['id'],[],report)
            else:
                storage.insert_report_revision(report)
        assert storage.list_report_revisions('m') == []
    finally:
        storage.close()


def test_export_preserves_layout_graph_chart_source_copy_and_escapes_all_text(tmp_path):
    from test_standalone_report_interactions import Templates
    payload = doc(chart(), graph(), block('TabGroup', options=[dict(label='Plan',text='Plan',source_segment_ids=['s']), dict(label='Risk',text='Risk',source_segment_ids=['s'])]),
                  layout=dict(op='grid',children=[dict(op='tabs',children=[dict(op='block',block_index=i) for i in range(2)]), dict(op='block',block_index=2)]))
    # In production, interaction nesting is reviewed below; test data can still contain escaped text.
    document = generate(payload)
    document.sections[0].blocks[0].data[0].label = '</text><script>alert(1)</script>'
    path = tmp_path / 'report.html'
    write_standalone_report(path, revision(document), transcript())
    html = path.read_text()
    assert html.count('<script>') == 1 and '&lt;/text&gt;&lt;script&gt;' in html
    assert 'data-layout-tabs' in html and 'data-observation="0"' in html and '<svg' in html
    assert 'marker-end=' in html and 'Delay causes Risk.' in html
    parser = Templates(); parser.feed(html)
    assert '12 USD' in parser.values['copy-full'] and 'Delay → Risk' in parser.values['copy-1']


def test_single_point_cannot_borrow_a_value_from_another_observation():
    candidate = chart()
    candidate['data'][0]['value'] = '6'
    candidate['data'][0]['evidence_quote'] = candidate['evidence_quote']
    with pytest.raises(ReportGenerationError): generate(doc(candidate))


@pytest.mark.parametrize('unit,quotes', [('%', ['Plan A 12%.', 'Plan B 6%.']), ('$', ['Plan A $12.', 'Plan B $6.'])])
def test_literal_percentages_and_prefix_currency_do_not_require_conversion(unit, quotes):
    segments = transcript()
    segments[0] = segments[0].model_copy(update={'text':' '.join(quotes)})
    candidate = block('BarChart', data=[dict(label=label,value=value,unit=unit,series='',evidence_quote=quote,source_segment_ids=['s']) for label,value,quote in zip(['Plan A','Plan B'],['12','6'],quotes)])
    report = generate_report(segments, language='en', client=Client(doc(candidate)))
    assert report.sections[0].blocks[0].data[0].unit == unit


def test_long_merged_topic_stays_readable_when_it_exceeds_layout_node_budget():
    from meetingbro.reports.design import compose_layout
    section = ReportSection.model_validate(doc(*(block(text=f'Plan {i}') for i in range(30)))['sections'][0])
    section.layout = compose_layout(section)
    validate_layout(section)
    assert section.layout.op == 'stack'
    section.blocks.extend(section.blocks[:10])
    assert compose_layout(section) is None
    document = ReportDocument(sections=[section.model_copy(update={'layout':None})])
    assert any(f.code == 'layout_budget' for f in review_design(document, repair=True))


def test_sankey_ribbon_thickness_is_proportional_without_invented_totals():
    from meetingbro.reports.sankey import sankey_geometry
    from meetingbro.schemas import ReportBlock
    candidate = ReportBlock.model_validate(block('SankeyDiagram',nodes=[dict(id=k,label=v,source_segment_ids=['s']) for k,v in [('a','Budget'),('b','Delivery'),('c','Research')]],edges=[dict(source='a',target=k,label='flows',relation='flow',value=value,unit='USD',evidence_quote=f'Budget to {label} flows {value} USD.',source_segment_ids=['s']) for k,label,value in [('b','Delivery','12'),('c','Research','6')]]))
    geometry = sankey_geometry(candidate)
    assert geometry['ribbons'][0]['width'] == pytest.approx(2 * geometry['ribbons'][1]['width'])
    assert geometry['nodes']['a']['height'] == pytest.approx(sum(r['width'] for r in geometry['ribbons']))
    assert all(r['path'].endswith(' Z') for r in geometry['ribbons'])


def test_deduplicated_visuals_merge_each_observation_source_and_rebuild_layout():
    from meetingbro.reports.generator import _deduplicate
    first = transcript()[0]
    second = first.model_copy(update={'id':'t'})
    left = chart(); right = copy.deepcopy(left)
    right['source_segment_ids'] = ['t']
    for datum in right['data']: datum['source_segment_ids'] = ['t']
    document = ReportDocument(**doc(left,right,layout=dict(op='grid',children=[dict(op='block',block_index=0),dict(op='block',block_index=1)])))
    _deduplicate(document, {'s':first,'t':second})
    assert len(document.sections[0].blocks) == 1 and document.sections[0].layout is None
    assert all(d.source_segment_ids == ['s','t'] for d in document.sections[0].blocks[0].data)



def test_directed_graph_cannot_reverse_an_explicit_causal_quote():
    candidate = graph()
    candidate['edges'][0]['source'], candidate['edges'][0]['target'] = 'b', 'a'
    with pytest.raises(ReportGenerationError): generate(doc(candidate))
