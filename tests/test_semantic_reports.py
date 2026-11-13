"""Behavioral coverage for editorial planning, provenance and renderer parity."""
import json
from datetime import datetime, timezone

import pytest

from meetingbro.reports.generator import ReportGenerationError, generate_report
from meetingbro.reports.standalone import write_standalone_report
from meetingbro.schemas import ReportDocument, ReportRevision, TranscriptSegment
from meetingbro.storage.db import Storage


def segment(key, text, speaker=None):
    return TranscriptSegment(id=key, meeting_id='m', text=text, speaker_id=speaker,
        start_time=0, end_time=60, original_language='zh', confidence=1, created_at=datetime.now(timezone.utc))


def block(kind, text, ids, **kwargs):
    return dict(type=kind, text=text, source_segment_ids=ids, title='', detail='', options=[], columns=[], rows=[], **kwargs)


def sample():
    transcript = [segment('a', 'Alice：甲方案每月 12 元，適合先試用。先試用，再檢討。', 'Alice'),
                  segment('b', 'Bob：乙方案每月 6 元，但功能較少。我傾向乙方案。尚未達成共識。', 'Bob')]
    prose = block('paragraph', '這次討論圍繞方案的取捨，價格與功能需要一併考慮。', ['a', 'b'])
    comparison = block('comparison', '講者對方案有不同取向。', ['a', 'b'])
    comparison['options'] = [dict(label='Alice · 甲方案', text='適合先試用。', source_segment_ids=['a']),
                             dict(label='Bob · 乙方案', text='功能較少，但傾向此方案。', source_segment_ids=['b'])]
    table = block('table', '逐字稿明確提及的月費。', ['a', 'b'])
    table['columns'] = ['方案', '每月費用']
    table['rows'] = [dict(cells=['甲方案', '12 元'], source_segment_ids=['a']),
                     dict(cells=['乙方案', '6 元'], source_segment_ids=['b'])]
    process = block('step', 'Alice 提出的評估次序。', ['a'])
    process['options'] = [dict(label='試用', text='先試用甲方案。', source_segment_ids=['a']),
                          dict(label='檢討', text='試用後再檢討。', source_segment_ids=['a'])]
    conclusion = block('conclusion', '尚未達成共識，Bob 的傾向不代表共同決定。', ['b'])
    payload = dict(title='方案取捨與下一步', executive_summary='討論聚焦價格與功能取捨，仍需釐清共同方向。',
        executive_summary_source_segment_ids=['a', 'b'], sections=[
        dict(id='tradeoffs', title='方案與立場', layout_type='mixed', blocks=[prose, comparison, table]),
        dict(id='next', title='評估方向', layout_type='mixed', blocks=[process, conclusion])])
    return transcript, payload


class Client:
    def __init__(self, *payloads):
        self.payloads = iter(payloads)
        self.calls = []
    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return json.dumps(next(self.payloads), ensure_ascii=False)


def test_one_chunk_has_semantic_sections_without_duplicating_body_in_hero():
    transcript, payload = sample()
    client = Client(payload)
    doc = generate_report(transcript, language='zh', client=client)
    assert len(client.calls) == 1
    assert [s.id for s in doc.sections] == ['tradeoffs', 'next']
    assert doc.takeaways == [] and doc.executive_summary == payload['executive_summary']
    options = doc.sections[0].blocks[1].options
    assert [o.source_segment_ids for o in options] == [['a'], ['b']]
    evidence = json.loads(client.calls[0]['user'])['transcript']
    assert [e['speaker_id'] for e in evidence] == ['Alice', 'Bob']


@pytest.mark.parametrize('case', ['summary_source', 'row_source', 'row_number', 'row_width', 'empty_table', 'option_number'])
def test_all_new_surfaces_reject_unsupported_evidence(case):
    transcript, payload = sample()
    table = payload['sections'][0]['blocks'][2]
    if case == 'summary_source': payload['executive_summary_source_segment_ids'] = ['missing']
    if case == 'row_source': table['rows'][0]['source_segment_ids'] = ['missing']
    if case == 'row_number': table['rows'][0]['cells'][1] = '6 元'  # Present elsewhere, but absent in this row's evidence.
    if case == 'row_width': table['rows'][0]['cells'].append('額外欄位')
    if case == 'empty_table': table['rows'] = []
    if case == 'option_number': payload['sections'][0]['blocks'][1]['options'][0]['text'] = '五十元'
    client = Client(payload)
    result = generate_report(transcript, language='zh', client=client)
    assert result.warnings and len(client.calls) == 1
    if case == 'summary_source':
        assert result.executive_summary != payload['executive_summary']
        assert 'missing' not in result.executive_summary_source_segment_ids
    else:
        rejected = payload['sections'][0]['blocks'][1 if case == 'option_number' else 2]
        assert all(b.type != rejected['type'] for b in result.sections[0].blocks)
        assert result.sections[1].blocks


def test_unsupported_executive_summary_sentence_is_omitted_with_a_visible_warning():
    transcript, payload = sample()
    payload['executive_summary'] = '每月 99 元。討論聚焦價格與功能取捨，仍需釐清共同方向。'
    doc = generate_report(transcript, language='zh', client=Client(payload))
    assert doc.executive_summary == '討論聚焦價格與功能取捨，仍需釐清共同方向。'
    assert doc.executive_summary_source_segment_ids == ['a', 'b']
    assert any(f.code == 'unsupported_prose_removed' and f.section_id == 'executive_summary'
               and f.repaired for f in doc.design_review)
    assert any('unsupported model claims' in warning for warning in doc.warnings)


def test_unsupported_section_heading_is_replaced_with_sourced_content():
    transcript, payload = sample()
    payload['sections'][0]['title'] = '99 元方案'
    doc = generate_report(transcript, language='zh', client=Client(payload))
    assert doc.sections[0].title != '99 元方案'
    assert any(f.code == 'unsupported_heading_removed' and f.repaired for f in doc.design_review)


def test_later_chunks_continue_topics_and_keep_distinct_speakers():
    first = segment('a', 'Supported view. ' + 'x' * 7970, 'Alice')
    second = segment('b', 'Supported view.', 'Bob')
    def part(key, text):
        return dict(title='Topic', executive_summary='', executive_summary_source_segment_ids=[], sections=[
            dict(id='topic', title='Topic', layout_type='text', blocks=[block('paragraph', text, [key])])])
    client = Client(part('a', 'Supported view.'), part('b', 'Supported view.'))
    doc = generate_report([first, second], language='en', client=client)
    assert len(client.calls) == 2 and len(doc.sections) == 1
    assert len(doc.sections[0].blocks) == 2  # Same words from different speakers are not silently collapsed.
    assert json.loads(client.calls[1]['user'])['previous_outline'][0]['id'] == 'topic'


def test_export_contains_semantic_markup_row_sources_and_complete_copy(tmp_path):
    from test_standalone_report_interactions import Templates
    transcript, payload = sample()
    # All output, including cells and headings, must be escaped.
    payload['sections'][0]['blocks'][2]['rows'][0]['cells'][0] = '<img src=x onerror=alert(1)>'
    doc = ReportDocument(**payload, generation_method='llm')
    report = ReportRevision(report_id='r', meeting_id='m', report_type='meeting', revision=1,
        document=doc, created_at=datetime.now(timezone.utc))
    path = tmp_path / 'report.html'
    write_standalone_report(path, report, transcript)
    html = path.read_text()
    assert '<table>' in html and '<th scope="col">方案</th>' in html
    assert '<ol class="items step count-2">' in html and '<ul class="items comparison count-2">' in html
    assert '<img src=x' not in html and '&lt;img src=x' in html
    assert 'Alice · 0:00' in html and 'Bob · 0:00' in html
    parser = Templates(); parser.feed(html)
    assert '12 元' in parser.values['copy-full'] and '6 元' in parser.values['copy-1']
    assert '尚未達成共識' in parser.values['copy-full']


@pytest.mark.parametrize('final_job', [False, True])
def test_row_sources_cannot_escape_meeting_at_storage_boundary(tmp_path, final_job):
    transcript, payload = sample()
    storage = Storage(tmp_path / 'reports.sqlite')
    storage.create_meeting('m')
    for item in transcript: storage.insert_segment(item)
    payload['sections'][0]['blocks'][2]['rows'][0]['source_segment_ids'] = ['foreign']
    report = ReportRevision(report_id='r', meeting_id='m', report_type='meeting', revision=1,
        document=ReportDocument(**payload, generation_method='llm'), created_at=datetime.now(timezone.utc))
    try:
        if final_job:
            job = storage.queue_final_summary('job', 'm', 'zh', None)
            storage.claim_final_summary_job()
            with pytest.raises(ValueError): storage.complete_final_summary_job(job['id'], [], report)
        else:
            with pytest.raises(ValueError): storage.insert_report_revision(report)
        assert storage.list_report_revisions('m') == []
    finally:
        storage.close()


def test_generated_title_replaces_history_preview_and_survives_restart(tmp_path, monkeypatch):
    import asyncio
    from meetingbro import main
    from fastapi import HTTPException
    from meetingbro.resources.coordinator import InferenceCoordinator
    transcript, payload = sample()
    path = tmp_path / 'history.sqlite'
    storage = Storage(path)
    storage.create_meeting('m')
    for item in transcript: storage.insert_segment(item)
    monkeypatch.setattr(main.app.state, 'storage', storage, raising=False)
    monkeypatch.setattr(main.app.state, 'report_locks', {}, raising=False)
    monkeypatch.setattr(main.app.state, 'inference_coordinator', InferenceCoordinator(), raising=False)
    client = Client(payload, {**payload, 'title': '方案評估進展'})
    monkeypatch.setattr(main.OpenAICompatibleClient, 'from_env', classmethod(lambda cls: client))
    async def run():
        assert (await main.list_meetings_endpoint(limit=100))[0].title == transcript[0].text
        report = await main.generate_meeting_report('m')
        assert (await main.list_meetings_endpoint(limit=100))[0].title == report.document.title
        newer = await main.generate_meeting_report('m')
        assert (await main.list_meetings_endpoint(limit=100))[0].title == newer.document.title == '方案評估進展'
        client.payloads = iter(['invalid'])
        with pytest.raises(HTTPException): await main.generate_meeting_report('m')
        assert (await main.list_meetings_endpoint(limit=100))[0].title == '方案評估進展'
    try:
        asyncio.run(run())
    finally:
        storage.close()
    reopened = Storage(path)
    try:
        assert reopened.list_meetings()[0]['report_title'] == '方案評估進展'
    finally:
        reopened.close()


def test_repeated_parts_and_nested_comparisons_are_consolidated():
    transcript, payload = sample()
    repeated = json.loads(json.dumps(payload['sections'][0]))
    repeated['id'] = 'another-part'
    repeated['title'] = '方案與立場  '
    # The same block also appears under a different title: output must not repeat it.
    payload['sections'].append(repeated)
    payload['sections'].append(dict(id='another-topic', title='重複整理', layout_type='text',
                                   blocks=[json.loads(json.dumps(repeated['blocks'][0]))]))
    doc = generate_report(transcript, language='zh', client=Client(payload))
    assert len(doc.sections) == 2
    assert [len(s.blocks) for s in doc.sections] == [3, 2]


def test_same_speaker_repeated_content_across_chunks_merges_citations():
    transcript = [segment('a', 'The plan. ' + 'x' * 7985, 'Alice'), segment('b', 'The plan.', 'Alice')]
    def part(key, topic):
        return dict(title='Planning', executive_summary='', executive_summary_source_segment_ids=[], sections=[
            dict(id=topic, title='Planning', layout_type='text', blocks=[block('paragraph', 'The plan.', [key])])])
    doc = generate_report(transcript, language='en', client=Client(part('a', 'planning'), part('b', 'part-two')))
    assert len(doc.sections) == 1 and len(doc.sections[0].blocks) == 1
    assert doc.sections[0].blocks[0].source_segment_ids == ['a', 'b']


def test_markdown_export_keeps_table_values(tmp_path):
    from meetingbro.exporter import export_meeting
    transcript, payload = sample()
    storage = Storage(tmp_path / 'export.sqlite')
    storage.create_meeting('m')
    for item in transcript: storage.insert_segment(item)
    storage.insert_report_revision(ReportRevision(report_id='r', meeting_id='m', report_type='meeting', revision=1,
        document=ReportDocument(**payload, generation_method='llm'), created_at=datetime.now(timezone.utc)))
    try:
        result = export_meeting(storage, meeting_id='m', export_root=tmp_path / 'exports')
        markdown = (tmp_path / 'exports' / result.export_dir / 'summary.md').read_text()
        assert '方案 | 每月費用' in markdown
        assert '甲方案 | 12 元' in markdown and '乙方案 | 6 元' in markdown
    finally:
        storage.close()


def test_unsupported_intro_text_is_removed_while_sourced_options_remain():
    transcript, payload = sample()
    comparison = payload['sections'][0]['blocks'][1]
    comparison['text'] = '十位同事的崗位不同，對觸控的評價也不同。'
    doc = generate_report(transcript, language='zh', client=Client(payload))
    saved = doc.sections[0].blocks[1]
    assert saved.text == '對觸控的評價也不同。'
    assert [option.label for option in saved.options] == ['Alice · 甲方案', 'Bob · 乙方案']
    assert any(f.code == 'unsupported_prose_removed' and f.repaired for f in doc.design_review)


def test_unsupported_option_clause_is_omitted_while_the_option_remains():
    transcript, payload = sample()
    option = payload['sections'][0]['blocks'][1]['options'][0]
    option['text'] = '適合先試用。需要另購 99 元配件。'
    doc = generate_report(transcript, language='zh', client=Client(payload))
    saved = doc.sections[0].blocks[1].options[0]
    assert saved.text == '適合先試用。'
    assert any(f.code == 'unsupported_prose_removed' and f.repaired for f in doc.design_review)
