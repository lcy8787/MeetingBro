"""Regression coverage for schema/validator agreement and lossless local repair."""
import asyncio
from datetime import datetime, timezone
import json
import sqlite3

from fastapi import HTTPException
from jsonschema import Draft202012Validator, ValidationError as SchemaError
import pytest

from meetingbro.reports.components import BLOCK_SCHEMA, REPORT_SCHEMA, generation_schema
from meetingbro.reports.generator import ReportGenerationError, generate_report
from meetingbro.reports.standalone import write_standalone_report
from meetingbro.schemas import ReportBlock, ReportRevision, TranscriptSegment
from meetingbro.storage.db import Storage


def segment():
    return TranscriptSegment(id='s', meeting_id='m', text='January cost 12 USD. February cost 6 USD. Cost overview.',
        start_time=0, end_time=10, original_language='en', confidence=1, created_at=datetime.now(timezone.utc))


def block(kind='KeyMetrics', **fields):
    return ReportBlock(type=kind, title='Costs', text='Cost overview.', source_segment_ids=['s'], **fields).model_dump()


def payload(item, *, legacy=False):
    if legacy:
        return {'blocks': [item]}
    return dict(title='Costs', executive_summary='', executive_summary_source_segment_ids=[], sections=[
        dict(id='costs', title='Costs', layout_type='statistic', blocks=[item], layout=None)], tool_request=None)


class Client:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return json.dumps(next(self.responses))


def datum():
    return dict(label='January', value='12', unit='USD', series='cost',
        evidence_quote='January cost 12 USD.', source_segment_ids=['s'])


def test_generated_schema_is_valid_and_families_are_disjoint():
    Draft202012Validator.check_schema(REPORT_SCHEMA)
    variants = BLOCK_SCHEMA['anyOf']
    names = [name for variant in variants for name in variant['properties']['type']['enum']]
    assert len(names) == len(set(names))
    Draft202012Validator(generation_schema([])).validate(payload(block()))


@pytest.mark.parametrize('kind,field,value', [
    ('KeyMetrics', 'data', [datum()]),
    ('EditorialText', 'data', [datum()]),
    ('BarChart', 'options', [dict(label='Cost', text='Cost overview.', source_segment_ids=['s'])]),
    ('SummaryCard', 'nodes', [dict(id='cost', label='Cost', text='', source_segment_ids=['s'])]),
    ('Timeline', 'cells', [dict(row='Cost', column='January', value='12', unit='USD', evidence_quote='January cost 12 USD.', source_segment_ids=['s'])]),
    ('KeyMetrics', 'columns', ['January', 'February']),
])
def test_provider_schema_rejects_payload_on_incompatible_component(kind, field, value):
    item = block(kind, **{field: value})
    if kind == 'BarChart':
        item['data'] = [datum(), dict(datum(), label='February', value='6')]
    with pytest.raises(SchemaError):
        Draft202012Validator(generation_schema([])).validate(payload(item))


@pytest.mark.parametrize('kind', ['statistic', 'KeyMetrics'])
@pytest.mark.parametrize('options', [[], [dict(label='Cost', text='Cost overview.', source_segment_ids=['s'])]])
@pytest.mark.parametrize('legacy', [False, True])
def test_nonnumeric_metrics_are_repaired_losslessly_without_another_call(tmp_path, kind, options, legacy):
    item = block(kind, options=options, detail='Cost overview.')
    client = Client(payload(item, legacy=legacy))
    doc = generate_report([segment()], language='zh', client=client)
    repaired = doc.sections[0].blocks[0].model_dump()
    assert repaired == dict(item, type='SummaryCard' if options else 'EditorialText')
    assert doc.sections[0].layout_type != 'statistic'
    assert len(client.calls) == 1
    assert any(f.code == 'metric_presentation_repaired' and f.repaired for f in doc.design_review)
    assert len(doc.warnings) == 1
    path = tmp_path / 'report.html'
    write_standalone_report(path, ReportRevision(report_id='r', meeting_id='m', report_type='meeting', revision=1,
        document=doc, created_at=datetime.now(timezone.utc)), [segment()])
    html = path.read_text()
    assert doc.warnings[0] in html and 'Cost overview.' in html
    assert 'statistic' not in doc.sections[0].blocks[0].type


@pytest.mark.parametrize('mutation,code,field', [
    ('data', 'invalid_report_component_data', '.data'),
    ('sources', 'invalid_report_sources', ''),
    ('option_sources', 'invalid_report_sources', ''),
    ('number', 'unsupported_report_claim', ''),
    ('quote', 'invalid_report_evidence', ''),
])
def test_metric_repair_never_bypasses_other_validation(mutation, code, field):
    item = block()
    if mutation == 'data': item['data'] = [datum()]
    if mutation == 'sources': item['source_segment_ids'] = ['unknown']
    if mutation == 'option_sources': item['options'] = [dict(label='Cost', text='Cost overview.', source_segment_ids=['unknown'])]
    if mutation == 'number': item['text'] = 'Cost 99 USD.'
    if mutation == 'quote': item['evidence_quote'] = 'An invented quotation.'
    client = Client(payload(item))
    with pytest.raises(ReportGenerationError) as failure:
        generate_report([segment()], language='en', client=client)
    assert failure.value.code == code
    assert failure.value.location == 'parts.0.sections.0.blocks.0' + field
    assert len(client.calls) == 1


def test_valid_numeric_metrics_and_charts_are_preserved():
    metric = block()
    metric['text'] = 'January cost 12 USD.'
    chart = block('BarChart', data=[datum(), dict(datum(), label='February', value='6', evidence_quote='February cost 6 USD.')])
    for item in [metric, chart]:
        Draft202012Validator(generation_schema([])).validate(payload(item))
        doc = generate_report([segment()], language='en', client=Client(payload(item)))
        assert doc.sections[0].blocks[0].model_dump() == item
        assert not doc.warnings


def test_specialized_payloads_remain_available_in_the_provider_schema():
    variants = [
        block('KnowledgeGraph', nodes=[dict(id=key, label=key, text='', source_segment_ids=['s']) for key in ['January', 'February']],
            edges=[dict(source='January', target='February', label='cost', relation='related', value='', unit='',
                evidence_quote='January cost 12 USD. February cost 6 USD.', source_segment_ids=['s'])]),
        block('RiskMatrix', cells=[dict(row='Cost', column=label, value='12', unit='USD',
            evidence_quote='January cost 12 USD.', source_segment_ids=['s']) for label in ['January', 'February']]),
        block('DataTable', columns=['Month', 'Cost'], rows=[dict(cells=[label, value], source_segment_ids=['s'])
            for label, value in [('January', '12 USD'), ('February', '6 USD')]]),
    ]
    validator = Draft202012Validator(generation_schema([]))
    for item in variants:
        validator.validate(payload(item))
        item['options'] = [dict(label='Cost', text='Cost overview.', source_segment_ids=['s'])]
        with pytest.raises(SchemaError):
            validator.validate(payload(item))


def test_legacy_prose_keeps_its_existing_strict_numeric_validation():
    item = block('EditorialText')
    item['text'] = 'Cost overview. Cost 99 USD.'
    with pytest.raises(ReportGenerationError) as failure:
        generate_report([segment()], language='en', client=Client(payload(item, legacy=True)))
    assert failure.value.code == 'unsupported_report_claim'


def test_repaired_metrics_recompose_the_layout_for_prose():
    response = payload(block())
    response['sections'][0]['layout'] = dict(op='grid', columns=2, children=[dict(op='block', block_index=0, span=6)])
    doc = generate_report([segment()], language='en', client=Client(response))
    assert doc.sections[0].layout_type == 'text'
    assert doc.sections[0].layout.children[0].span == 12


def test_forbidden_extra_keys_are_not_logged_or_exposed_as_locations(caplog):
    item = block()
    item['private meeting phrase'] = 'private model response'
    with pytest.raises(ReportGenerationError) as failure:
        generate_report([segment()], language='en', client=Client(payload(item)))
    assert failure.value.code == 'invalid_report_structure'
    assert failure.value.location == 'parts.0.sections.0.blocks.0.field'
    assert 'private meeting phrase' not in caplog.text
    assert 'private model response' not in caplog.text


def test_manual_failure_exposes_safe_location_and_preserves_previous_report(tmp_path, monkeypatch):
    from meetingbro import main
    from meetingbro.resources.coordinator import InferenceCoordinator
    db = Storage(tmp_path / 'manual.sqlite')
    db.create_meeting('m'); db.insert_segment(segment())
    monkeypatch.setattr(main.app.state, 'storage', db, raising=False)
    monkeypatch.setattr(main.app.state, 'report_locks', {}, raising=False)
    monkeypatch.setattr(main.app.state, 'inference_coordinator', InferenceCoordinator(), raising=False)
    client = Client(payload(block()), payload(block('EditorialText', data=[datum()])))
    monkeypatch.setattr(main.OpenAICompatibleClient, 'from_env', classmethod(lambda cls: client))
    async def run():
        original = await main.generate_meeting_report('m')
        with pytest.raises(HTTPException) as failure:
            await main.generate_meeting_report('m')
        assert failure.value.status_code == 502
        assert failure.value.detail['code'] == 'invalid_report_component_data'
        assert failure.value.detail['location'] == 'parts.0.sections.0.blocks.0.data'
        assert db.list_report_revisions('m') == [original]
        assert len(client.calls) == 2
    try:
        asyncio.run(run())
    finally:
        db.close()


def test_background_failure_waits_for_explicit_retry_and_clears_diagnostics(tmp_path, monkeypatch):
    from meetingbro.jobs.final_summary import FinalSummaryWorker
    from meetingbro.llm.openai_compatible import OpenAICompatibleClient
    db = Storage(tmp_path / 'jobs.sqlite')
    db.create_meeting('m'); db.insert_segment(segment())
    client = Client(payload(block('EditorialText', data=[datum()])), payload(block()))
    monkeypatch.setattr(OpenAICompatibleClient, 'from_env', classmethod(lambda cls: client))
    async def wait(status):
        async with asyncio.timeout(3):
            while db.get_final_summary_job('m')['status'] != status:
                await asyncio.sleep(.01)
        return db.get_final_summary_job('m')
    async def run():
        worker = FinalSummaryWorker(db, lambda: None, report_only=True)
        worker.start()
        try:
            await worker.enqueue('m', 'en')
            job = await wait('failed')
            assert job['error_code'] == 'invalid_report_component_data'
            assert job['error_location'] == 'parts.0.sections.0.blocks.0.data'
            assert not db.list_report_revisions('m')
            await asyncio.sleep(.04)
            assert len(client.calls) == 1 and db.get_final_summary_job('m')['attempts'] == 1
            queued = await worker.enqueue('m', 'en')
            assert queued['error'] is queued['error_code'] is queued['error_location'] is None
            succeeded = await wait('succeeded')
            assert succeeded['error'] is succeeded['error_code'] is succeeded['error_location'] is None
            assert succeeded['attempts'] == 2 and len(client.calls) == 2
            assert len(db.list_report_revisions('m')) == 1
        finally:
            await worker.close()
    try:
        asyncio.run(run())
    finally:
        db.close()


def test_migrates_existing_jobs_without_losing_errors_or_history(tmp_path):
    path = tmp_path / 'v4.sqlite'
    db = Storage(path)
    db.create_meeting('m'); db.insert_segment(segment())
    db.queue_final_summary('j', 'm', 'en', None)
    db.claim_final_summary_job()
    db.fail_final_summary_job('j', 'Previous failure')
    saved_segments = db.list_segments('m')
    db.close()
    # Recreate the exact pre-upgrade column layout, preserving existing rows.
    with sqlite3.connect(path) as conn:
        conn.execute('ALTER TABLE final_summary_jobs DROP COLUMN error_code')
        conn.execute('ALTER TABLE final_summary_jobs DROP COLUMN error_location')
        conn.execute('DELETE FROM meetingbro_migrations WHERE version=5')
    for _ in range(2):
        db = Storage(path)
        try:
            job = db.get_final_summary_job('m')
            assert job['status'] == 'failed' and job['error'] == 'Previous failure'
            assert job['attempts'] == 1 and job['error_code'] is job['error_location'] is None
            assert db.list_segments('m') == saved_segments
        finally:
            db.close()
