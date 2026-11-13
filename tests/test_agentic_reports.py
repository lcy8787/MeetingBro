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
    variants = REPORT_SCHEMA['properties']['sections']['items']['properties']['blocks']['items']['anyOf']
    assert all(schema['additionalProperties'] is False for schema in variants)
    assert all('title' in schema['properties'] and 'title' in schema['required'] for schema in variants)
    assert set(names) <= {name for schema in variants for name in schema['properties']['type']['enum']}


@pytest.mark.parametrize('name', ['list_components', 'validate_structure', 'execute_javascript'])
def test_tool_requests_end_the_part_without_another_call(name):
    client = Client(tool(name), doc())
    with pytest.raises(ReportGenerationError):
        generate_report(transcript(), language='en', client=client)
    assert len(client.calls) == 1


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


@pytest.mark.parametrize('mutation', ['number', 'unit', 'label', 'source', 'nonliteral', 'nan', 'empty', 'order'])
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


@pytest.mark.parametrize('speaker', [None,'Alice'])
def test_model_schema_only_allows_real_metadata_speaker_ids(speaker):
    segments=transcript()
    segments[0]=segments[0].model_copy(update={'speaker_id':speaker})
    client=Client(doc())
    generate_report(segments,language='en',client=client)
    call=client.calls[0]
    assert 'speaker_id' not in json.dumps(call['response_schema'])
    assert json.loads(call['user'])['transcript'][0]['speaker_id'] == speaker


def test_speaker_schema_is_request_local_and_never_leaks_another_meetings_ids():
    from meetingbro.reports.components import generation_schema, REPORT_SCHEMA
    first=generation_schema(['Alice']); second=generation_schema([])
    def speaker(schema):
        return schema['properties']['sections']['items']['properties']['blocks']['items']['anyOf'][0]['properties']['speaker_id']
    assert speaker(first)['enum']==['','Alice']
    assert speaker(second)['enum']==['']
    assert 'enum' not in speaker(REPORT_SCHEMA)


def test_unidentified_speaker_does_not_prevent_publishing_independent_content():
    result = generate(doc(block(speaker_id='Bob'), block('ConclusionPanel')))
    assert [b.type for b in result.sections[0].blocks] == ['ConclusionPanel']
    assert result.warnings


def test_speaker_failures_have_a_diagnostic_code_without_weakening_validation():
    with pytest.raises(ReportGenerationError) as failure:
        generate(doc(block(speaker_id='Bob')))
    assert failure.value.code=='invalid_report_speaker'


def test_invalid_json_is_diagnosable_without_logging_transcript_content(caplog):
    class Invalid:
        def chat(self,**kwargs): return '{"secret meeting phrase":'
    with pytest.raises(ReportGenerationError) as failure:
        generate_report(transcript(),language='en',client=Invalid())
    assert failure.value.code=='invalid_report_json'
    assert 'invalid_report_json' in caplog.text
    assert 'secret meeting phrase' not in caplog.text


def test_structure_failures_log_field_locations_instead_of_model_content(caplog):
    payload=doc(block(type='not-a-real-component'))
    payload['sections'][0]['blocks'][0]['text']='private transcript phrase'
    with pytest.raises(ReportGenerationError) as failure: generate(payload)
    assert failure.value.code=='invalid_report_structure'
    assert 'sections.0.blocks.0.type' in caplog.text
    assert 'private transcript phrase' not in caplog.text


def test_long_reports_accept_more_than_twelve_blocks_within_the_published_schema():
    segments=transcript()
    segments[0]=segments[0].model_copy(update={'text':' '.join(f'Observation {i}.' for i in range(16))})
    payload=doc()
    payload['sections']=[dict(id=topic,title=topic,layout_type='text',blocks=[block(text=f'Observation {i}.') for i in range(start,start+8)]) for topic,start in [('Overview',0),('Discussion',8)]]
    result=generate_report(segments,language='en',client=Client(payload))
    assert sum(len(section.blocks) for section in result.sections)==16
    assert all(section.layout for section in result.sections)


def test_accepting_longer_reports_does_not_bypass_per_block_provenance_or_section_bounds():
    segments=transcript()
    segments[0]=segments[0].model_copy(update={'text':' '.join(f'Observation {i}.' for i in range(16))})
    payload=doc()
    payload['sections']=[dict(id=topic,title=topic,layout_type='text',blocks=[block(text=f'Observation {i}.') for i in range(start,start+8)]) for topic,start in [('Overview',0),('Discussion',8)]]
    missing=copy.deepcopy(payload);missing['sections'][1]['blocks'][5]['source_segment_ids']=[]
    over=copy.deepcopy(payload);over['sections'][0]['blocks'].append(block(text='Observation 0.'))
    for invalid, count in [(missing, 15), (over, 8)]:
        result = generate_report(segments, language='en', client=Client(invalid))
        assert sum(len(section.blocks) for section in result.sections) == count
        assert result.warnings


def test_three_call_budget_reserves_a_completion_for_each_transcript_part():
    segments=[transcript()[0].model_copy(update={'id':key,'text':'Plan. '+('x'*7985)}) for key in ['a','b','c']]
    def part(key):
        payload=doc(block(source_segment_ids=[key]))
        payload['sections'][0]['id']=key
        payload['sections'][0]['title']=key
        return payload
    client=Client(*(part(key) for key in ['a','b','c']))
    result=generate_report(segments,language='en',client=client,max_model_calls=3)
    assert len(client.calls)==3 and result.sections
    assert all(call['single_attempt'] and 'tool_request' not in call['response_schema']['properties'] for call in client.calls)


def test_insufficient_generation_budget_fails_before_any_model_call():
    segments=[transcript()[0].model_copy(update={'text':'Plan. '+('x'*17000)})]
    client=Client(doc())
    with pytest.raises(ValueError,match='budget'):
        generate_report(segments,language='en',client=client,max_model_calls=2)
    assert client.calls==[]


def test_only_unsupported_prose_sentences_are_omitted_with_visible_audit_notice():
    segments=transcript()
    segments[0]=segments[0].model_copy(update={'text':'The release supports device 11. It uses an improved scheduler.'})
    payload=doc(block(text='排程器已改善，支援裝置 11。它也支援版本 26。'))
    result=generate_report(segments,language='zh',client=Client(payload))
    assert result.sections[0].blocks[0].text=='排程器已改善，支援裝置 11。'
    assert any(f.code=='unsupported_prose_removed' and f.repaired for f in result.design_review)
    assert result.warnings


def test_prose_repair_never_fabricates_replacements_or_silently_accepts_an_invalid_block():
    segments=transcript()
    segments[0]=segments[0].model_copy(update={'text':'Device 11 uses an improved scheduler.'})
    for candidate in [block(text='裝置 26。'),block('BarChart',text='裝置 11。裝置 26。'),block(text='裝置 11。裝置 26。',source_segment_ids=['missing'])]:
        with pytest.raises(ReportGenerationError):
            generate_report(segments,language='zh',client=Client(doc(candidate)))


def test_an_unsupported_count_in_a_prose_heading_is_omitted_without_losing_the_body():
    segments=transcript()
    segments[0]=segments[0].model_copy(update={'text':'Parents care about content, communication, and guidance.'})
    payload=doc(block(title='三個家長關切',text='家長關心內容、溝通與引導。'))
    report=generate_report(segments,language='zh',client=Client(payload))
    assert report.sections[0].blocks[0].title==''
    assert report.sections[0].blocks[0].text=='家長關心內容、溝通與引導。'
    assert report.design_review[0].code=='unsupported_heading_removed'
    assert report.warnings


def test_action_and_decision_lists_are_grounded_by_their_options_without_a_redundant_quote():
    actions = block('ActionItems', text='Assigned work.', options=[
        dict(label='Review', text='Alice will review Plan.', source_segment_ids=['s'])])
    decisions = block('DecisionPanel', text='Agreed decision.', options=[
        dict(label='Launch', text='We agree to Launch.', source_segment_ids=['s'])])
    result = generate(doc(actions, decisions))
    assert result.sections[0].blocks[0].type == 'ActionItems'
    assert result.sections[0].blocks[1].type == 'DecisionPanel'
    # A supplied block-level quote is still checked verbatim.
    broken = block('ActionItems', text='Assigned work.', evidence_quote='We agree to Wait.', options=[
        dict(label='Review', text='Alice will review Plan.', source_segment_ids=['s'])])
    with pytest.raises(ReportGenerationError):
        generate(doc(broken))
    # A single decision statement without options still needs exact support.
    paraphrase = block('DecisionPanel', text='The team decided to ship immediately.')
    with pytest.raises(ReportGenerationError):
        generate(doc(paraphrase))


def test_output_truncation_ends_the_part_without_retry():
    from meetingbro.llm.openai_compatible import LLMResponseError
    class TruncatingClient:
        def __init__(self):
            self.calls = []
        def chat(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                raise LLMResponseError('output_truncated', 'truncated')
            return json.dumps(doc())
    client = TruncatingClient()
    with pytest.raises(ReportGenerationError) as failure:
        generate_report(transcript(), language='en', client=client)
    assert failure.value.code == 'output_truncated'
    assert len(client.calls) == 1


def test_invalid_json_never_triggers_a_hidden_paid_retry():
    class InvalidClient:
        def __init__(self):
            self.calls = 0
        def chat(self, **kwargs):
            self.calls += 1
            return '{"sections": ['
    client = InvalidClient()
    with pytest.raises(ReportGenerationError) as failure:
        generate_report(transcript(), language='en', client=client)
    assert failure.value.code == 'invalid_report_json' and client.calls == 1


def test_agent_uses_configured_output_budget_from_the_wrapped_client():
    class BudgetedClient:
        max_output_tokens = 11000
        def __init__(self): self.calls = []
        def chat(self, **kwargs):
            self.calls.append(kwargs)
            return json.dumps(doc())
    client = BudgetedClient()
    generate_report(transcript(), language='en', client=client)
    assert client.calls[0]['max_tokens'] == 11000


def test_duplicate_chart_observations_are_removed_losslessly():
    candidate = chart('DataBars')
    candidate['data'].append(copy.deepcopy(candidate['data'][0]))
    result = generate(doc(candidate))
    block = result.sections[0].blocks[0]
    assert block.type == 'DataBars'
    assert len(block.data) == 3
    assert any(f.code == 'duplicate_observation_removed' and f.repaired for f in result.design_review)


def test_one_point_chart_is_degraded_to_a_sourced_metric_card():
    candidate = chart('DataBars')
    candidate['data'] = [dict(candidate['data'][0]), dict(candidate['data'][0])]
    result = generate(doc(candidate))
    block = result.sections[0].blocks[0]
    assert block.type == 'KeyMetrics' and block.data == []
    assert len(block.options) == 1 and block.options[0].source_segment_ids == ['s']
    assert any(f.code == 'chart_presentation_repaired' and f.repaired for f in result.design_review)
    assert any('one-point chart' in warning for warning in result.warnings)


def test_conflicting_duplicate_chart_values_still_fail_closed():
    candidate = chart('DataBars')
    duplicate = copy.deepcopy(candidate['data'][0])
    duplicate['value'] = '99'
    candidate['data'].append(duplicate)
    with pytest.raises(ReportGenerationError):
        generate(doc(candidate))
