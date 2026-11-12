import asyncio
import json
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from meetingbro.reports.generator import ReportGenerationError, _chunks, generate_report
from meetingbro.schemas import TranscriptSegment
from meetingbro.storage.db import Storage


def segment(text='Sales were 12 units', segment_id='s'):
    return TranscriptSegment(id=segment_id, meeting_id='m', start_time=0, end_time=1,
                             text=text, original_language='en', confidence=1,
                             created_at=datetime.now(timezone.utc))


class Client:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def test_generation_requires_real_citations_and_supported_numbers():
    valid = Client(json.dumps({'blocks': [{'type': 'bullet', 'text': 'Sales: 12 units', 'source_segment_ids': ['s']}]}))
    doc = generate_report([segment()], language='en', client=valid)
    assert doc.generation_method == 'llm'
    assert doc.sections[0].blocks[0].source_segment_ids == ['s']
    for text, citation in [('Sales: 99 units', 's'), ('Sales: 12 units', 'wrong')]:
        client = Client(json.dumps({'blocks': [{'type': 'bullet', 'text': text, 'source_segment_ids': [citation]}]}))
        with pytest.raises(ReportGenerationError):
            generate_report([segment()], language='en', client=client)


def test_long_individual_segment_is_not_truncated():
    text = 'a' * 17000
    chunks = _chunks([segment(text)])
    assert ''.join(piece for chunk in chunks for _, piece in chunk) == text
    assert len(chunks) == 3
    with pytest.raises(ReportGenerationError, match='No summary model'):
        generate_report([segment(text)], language='en')


def test_report_endpoint_persists_new_revisions_and_handles_missing_transcript(tmp_path, monkeypatch):
    from meetingbro.main import app, OpenAICompatibleClient

    storage = Storage(tmp_path / 'db.sqlite')
    storage.create_meeting('m')
    storage.create_meeting('empty')
    storage.insert_segment(segment())
    app.state.storage = storage
    app.state.report_locks = {}
    from meetingbro.resources.coordinator import InferenceCoordinator
    app.state.inference_coordinator = InferenceCoordinator()
    monkeypatch.setattr(OpenAICompatibleClient, 'from_env', classmethod(lambda cls: None))

    async def exercise():
        from meetingbro.main import generate_meeting_report, list_reports
        with pytest.raises(HTTPException) as missing:
            await generate_meeting_report('absent')
        assert missing.value.status_code == 404
        with pytest.raises(HTTPException) as empty:
            await generate_meeting_report('empty')
        assert empty.value.status_code == 422
        with pytest.raises(HTTPException) as unconfigured:
            await generate_meeting_report('m')
        assert unconfigured.value.status_code == 503
        assert storage.list_report_revisions('m') == []
        client = Client(json.dumps({'blocks': [{'type': 'bullet', 'text': 'Sales: 12 units', 'source_segment_ids': ['s']}]}))
        monkeypatch.setattr(OpenAICompatibleClient, 'from_env', classmethod(lambda cls: client))
        first = await generate_meeting_report('m')
        second = await generate_meeting_report('m')
        assert {first.revision, second.revision} == {1, 2}
        listing = await list_reports('m')
        assert [r.revision for r in listing] == [1, 2]
        assert listing[0].document.sections[0].blocks[0].source_segment_ids == ['s']
    asyncio.run(exercise())
    storage.close()


def test_invalid_reports_never_trigger_hidden_paid_repairs():
    valid = json.dumps({'blocks': [{'type': 'timeline', 'title': '2026年', 'text': '2026年開始', 'source_segment_ids': ['s']}]}, ensure_ascii=False)
    client = Client(valid)
    doc = generate_report([segment('2026年開始')], language='zh', client=client)
    assert len(client.calls) == 1 and doc.generation_method == 'llm'
    assert doc.sections[0].title == '2026年' and doc.sections[0].layout_type == 'timeline'
    assert client.calls[0]['response_schema']['properties']['sections']['items']['properties']['blocks']['maxItems'] == 8
    invalid = json.dumps({'blocks': [{'type': 'timeline', 'title': '2027年', 'text': '2027年開始', 'source_segment_ids': ['s']}]}, ensure_ascii=False)
    for payload in (invalid, 'not JSON', ''):
        client = Client(payload)
        with pytest.raises(ReportGenerationError):
            generate_report([segment('2026年開始')], language='zh', client=client)
        assert len(client.calls) == 1


def test_interactive_options_require_sources_and_supported_numbers():
    block = {'type':'comparison', 'title':'Compare plans', 'text':'Discussed plans', 'source_segment_ids':['s'],
             'options':[{'label':'Plan A','text':'12 units', 'source_segment_ids':['s']},
                        {'label':'Plan B','text':'6 units', 'source_segment_ids':['s']}]}
    client = Client(json.dumps({'blocks':[block]}))
    doc = generate_report([segment('Plan A needs 12 units; Plan B needs 6 units.')], language='en', client=client)
    assert doc.generation_method == 'llm'
    assert [option.label for option in doc.sections[0].blocks[0].options] == ['Plan A', 'Plan B']
    for invalid in ({'text':'99 units'}, {'source_segment_ids':['other']}, {'source_segment_ids':[]}):
        bad = json.loads(json.dumps(block))
        bad['options'][0].update(invalid)
        with pytest.raises(ReportGenerationError):
            generate_report([segment('Plan A needs 12 units; Plan B needs 6 units.')], language='en',
                            client=Client(json.dumps({'blocks':[bad]})))


def test_openrouter_report_endpoint_uses_configured_remote_client(tmp_path, monkeypatch):
    from meetingbro import main
    from meetingbro.resources.coordinator import InferenceCoordinator
    storage = Storage(tmp_path / 'remote.sqlite')
    storage.create_meeting('m'); storage.insert_segment(segment())
    main.app.state.storage = storage
    main.app.state.report_locks = {}
    main.app.state.inference_coordinator = InferenceCoordinator()
    client = Client(json.dumps({'blocks':[{'type':'conclusion','text':'12 units','source_segment_ids':['s']}]}))
    monkeypatch.setenv('MEETINGBRO_LLM_BASE_URL', 'https://openrouter.ai/api/v1')
    monkeypatch.setattr(main.OpenAICompatibleClient, 'from_env', classmethod(lambda cls: client))
    report = asyncio.run(main.generate_meeting_report('m'))
    assert client.calls and report.document.generation_method == 'llm'
    assert storage.list_report_revisions('m')[0].document.sections[0].blocks[0].text == '12 units'
    storage.close()


def test_chinese_spoken_statistics_are_validated_without_requiring_arabic_digits():
    evidence = '三十分鐘烤機，最高溫度四十七點八度。屏幕能激發一千八百尼特亮度。'
    for text in ('三十分鐘烤機，最高四十七點八度。', '屏幕亮度一千八百尼特。'):
        client = Client(json.dumps({'blocks': [{'type': 'statistic', 'text': text, 'source_segment_ids': ['s']}]}))
        doc = generate_report([segment(evidence)], language='zh', client=client)
        assert doc.generation_method == 'llm' and len(client.calls) == 1
    for text in ('最高五十七點八度。', '屏幕亮度三千尼特。'):
        client = Client(json.dumps({'blocks': [{'type': 'statistic', 'text': text, 'source_segment_ids': ['s']}]}))
        with pytest.raises(ReportGenerationError):
            generate_report([segment(evidence)], language='zh', client=client)
        assert len(client.calls) == 1


@pytest.mark.parametrize('source,text', [
    ('Apps launch up to 30 percent faster.','App 啟動速度最高提升 30%。'),
    ('This helps tens of thousands of students.','協助數以萬計的學生。'),
    ('We refined it over the past year.','在過去一年持續改進。'),
    ('The naming story was unresolved.','命名故事一度懸而未解。'),
    ('Cost is 1,000 units.','成本為 1000。'),
    ('Temperature is forty-seven point eight degrees.','溫度四十七點八度。'),
    ('The increase is thirty percent.','提升百分之三十。'),
])
def test_numeric_validation_accepts_literal_translation_and_format_equivalence(source,text):
    from meetingbro.reports.generator import _validate_text
    _validate_text(text,['s'],{'s':source})


@pytest.mark.parametrize('source,text', [
    ('30 items.','提升30%。'),
    ('30 percent faster.','提升31%。'),
    ('Thousands of students.','萬名學生。'),
    ('Tens of thousands of students.','10000 名學生。'),
    ('Temperature is forty-seven point eight degrees.','溫度五十七點八度。'),
    ('12 units.','99 units.'),
    ('Cost 1200 units.','1.2 units.'),
    ('Temperature 1 degree.','溫度負一度。'),
])
def test_numeric_translation_checks_still_reject_invented_values_ranges_and_percentages(source,text):
    from meetingbro.reports.generator import _validate_text
    with pytest.raises(ValueError): _validate_text(text,['s'],{'s':source})


def test_indefinite_and_distributive_grammar_does_not_create_false_numeric_failures():
    from meetingbro.reports.generator import _validate_text
    _validate_text('提供家長一個有用的起點。',['s'],{'s':'Giving parents a helpful starting point.'})
    _validate_text('我們相信每一個應用程式都重要。',['s'],{'s':'We believe every app matters.'})


@pytest.mark.parametrize('text', ['最高1度。','最高一度。','只有一個 GPU。'])
def test_an_indefinite_article_cannot_support_an_invented_metric_of_one(text):
    from meetingbro.reports.generator import _validate_text
    with pytest.raises(ValueError):
        _validate_text(text,['s'],{'s':'The temperature is 0 degrees with 2 GPUs in a device.'})


@pytest.mark.parametrize('source,text',[
    ('2 million accounts.','二百萬個帳號。'),
    ('1.2 billion devices.','十二億部裝置。'),
    ('2千萬人。','20000000 人。'),
    ('Hundreds of millions of people.','數億人。'),
    ('One hundred and twenty-one units.','121 units.'),
])
def test_explicit_numeric_place_values_are_equivalent_without_rounding(source,text):
    from meetingbro.reports.generator import _validate_text
    _validate_text(text,['s'],{'s':source})


@pytest.mark.parametrize('source,text',[
    ('one and one','2'),
    ('twenty thirty','50'),
    ('half a million','1000000'),
    ('1.2 billion devices','1200000 devices'),
])
def test_spoken_lists_and_scales_never_authorize_invented_arithmetic(source,text):
    from meetingbro.reports.generator import _validate_text
    with pytest.raises(ValueError): _validate_text(text,['s'],{'s':source})
