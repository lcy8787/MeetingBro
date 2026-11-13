import json
import urllib.error
from datetime import datetime, timezone

import pytest

from meetingbro.llm.openai_compatible import OpenAICompatibleClient, OpenAICompatibleConfig
from meetingbro.reports.generator import generate_report, ReportGenerationError
from meetingbro.reports.standalone import write_standalone_report
from meetingbro.schemas import ReportDocument, ReportRevision, TranscriptSegment
from meetingbro.storage.db import Storage


def segment(key, text, start=0):
    return TranscriptSegment(id=key, meeting_id='m', start_time=start, end_time=start + 30,
                             text=text, original_language='zh', confidence=1, created_at=datetime.now(timezone.utc))


def item(kind, text, key, **fields):
    return dict(kind=kind, title='要點', text=text, source_segment_ids=[key], **fields)


def entry(key, label, text):
    return dict(label=label, text=text, evidence_quote=text, source_segment_ids=[key])


def answer(items):
    return json.dumps(dict(title='測試', executive_summary='', executive_summary_source_segment_ids=[],
                           sections=[dict(id='topic', title='討論', items=items)]))


class Replay:
    def __init__(self, *answers):
        self.answers = iter(answers)
        self.calls = 0

    def chat(self, **kwargs):
        self.calls += 1
        return next(self.answers)


def test_tasks_branches_and_multiple_perspectives_render_with_individual_sources(tmp_path):
    deadline = '我會在星期三中午交給 Amy。'
    conditions = ['修好便如期發佈。', '否則順延一星期。']
    views = [('設計', '點選圖層較快。'), ('開發', '適合調試。'), ('剪接', '需要鍵盤。'), ('攝影', '方便對焦。')]
    rows = [segment('a', 'Ben：' + deadline), segment('b', ''.join(conditions)),
            *(segment(name, view) for name, view in views)]
    tasks = item('tasks', '測試清單交付', 'a', entries=[entry('a', 'Ben', deadline)])
    branches = item('conditions', '發佈視乎修復進度。', 'b',
                    entries=[entry('b', name, text) for name, text in zip(['修好','否則'], conditions)])
    perspectives = item('perspectives', '按崗位比較。', '設計', entries=[entry(name,name,view) for name,view in views])
    perspectives['source_segment_ids'] = [name for name, _ in views]
    doc = generate_report(rows, language='zh', client=Replay(answer([tasks, branches, perspectives])))
    assert not doc.warnings
    blocks = doc.sections[0].blocks
    assert [b.type for b in blocks] == ['ActionItems', 'ComparisonCards', 'PerspectiveCards']
    assert blocks[0].options[0].label == 'Ben' and blocks[0].options[0].text == deadline
    assert [o.source_segment_ids for o in blocks[-1].options] == [[name] for name,_ in views]
    report = ReportRevision(report_id='r',meeting_id='m',revision=1,report_type='meeting',created_at=rows[0].created_at,document=doc)
    path = tmp_path/'demo.html'
    write_standalone_report(path, report, rows)
    html = path.read_text()
    assert 'report-action-list' in html and 'items comparison count-3' in html


def test_metrics_use_only_comparable_observations_and_keep_independent_supported_prose():
    observations = [('A','12','kg'),('B','15','kg'),('C','18','°C')]
    quotes = [f'{label} {value} {unit}.' for label,value,unit in observations]
    row = segment('a', 'Measured in the lab. ' + ' '.join(quotes))
    data = [dict(label=label,value=value,unit=unit,evidence_quote=quote,source_segment_ids=['a'])
            for (label,value,unit),quote in zip(observations,quotes)]
    valid = item('measurements', 'Measured in the lab.', 'a', entries=data)
    bad = item('measurements', 'Measured in the lab.', 'a', observations=[dict(data[0], value='99')])
    doc = generate_report([row], language='en', client=Replay(answer([valid,bad])))
    blocks = doc.sections[0].blocks
    assert [b.type for b in blocks] == ['BarChart', 'KeyMetrics', 'EditorialText']
    assert [d.unit for d in blocks[0].data] == ['kg','kg']
    assert blocks[1].options[0].text == '18 °C'
    assert blocks[2].text == 'Measured in the lab.' and '99' not in doc.model_dump_json()
    assert doc.warnings


def test_partial_report_survives_storage_and_export_with_original_revision(tmp_path):
    rows = [segment(key,'recording '+ 'x'*7985,i*30) for i,key in enumerate('abc')]
    client = Replay(answer([item('explanation','recording','a')]), '{"outer":{"inner":{}}',
                    answer([item('conclusion','recording','c')]))
    doc = generate_report(rows,language='zh',client=client)
    assert client.calls == 3 and doc.coverage == 'partial'
    assert [(p.part,p.start_time,p.end_time,p.reason) for p in doc.missing_parts] == [(2,30,60,'invalid_report_json')]
    storage=Storage(tmp_path/'demo.db')
    try:
        storage.create_meeting('m')
        for row in rows: storage.insert_segment(row)
        revision=ReportRevision(meeting_id='m',report_id='r',report_type='meeting',revision=1,
                                document=ReportDocument(generation_method='llm'),created_at=rows[0].created_at)
        storage.insert_report_revision(revision)
        storage.insert_report_revision(revision.model_copy(update={'revision':2,'document':doc}))
        old,new=storage.list_report_revisions('m')
        assert old.document.coverage=='complete'
        path=tmp_path/'partial.html'
        write_standalone_report(path,new,rows)
        assert '部分摘要' in path.read_text() and '00:30–01:00' in path.read_text()
    finally:
        storage.close()


@pytest.mark.parametrize('raw,code',[('{"outer":{"inner":{}}','invalid_report_json'),
    ('{} and {}','ambiguous_response'), ('[{"blocks":[]}]','invalid_report_json')])
def test_nested_or_ambiguous_response_never_publishes(raw,code):
    with pytest.raises(ReportGenerationError) as error:
        generate_report([segment('a','word')],language='en',client=Replay(raw))
    assert error.value.code==code


@pytest.mark.parametrize('failure', ['timeout','format','length','empty'])
def test_one_actual_http_attempt_for_every_failure(monkeypatch,failure):
    import io
    import urllib.request
    calls=[]
    def send(req,**kwargs):
        calls.append(req)
        if failure=='timeout': raise TimeoutError()
        if failure=='format': raise urllib.error.HTTPError(req.full_url,400,'',{},io.BytesIO(b'unsupported response_format'))
        choice={'finish_reason':'length' if failure=='length' else 'stop',
                'message':{'content':'{"a":{"b":{}}' if failure=='length' else '', 'reasoning_content':'{}'}}
        return io.BytesIO(json.dumps({'choices':[choice]}).encode())
    monkeypatch.setattr(urllib.request,'urlopen',send)
    client=OpenAICompatibleClient(OpenAICompatibleConfig('test','http://localhost','test',max_attempts=3))
    with pytest.raises(ReportGenerationError) as error:
        generate_report([segment('a','word')],language='en',client=client)
    assert len(calls)==1
    assert error.value.code=={'timeout':'model_timeout','format':'structured_output_unsupported',
                              'length':'output_truncated','empty':'empty_response'}[failure]


def test_split_evidence_extends_only_across_a_verified_boundary():
    rows = [segment('a', '甲組認為多工。'), segment('b', '已經改善。乙組覺得操作繁複。')]
    views = item('perspectives', '兩組意見。', 'a', entries=[
        entry('a','甲組','甲組認為多工已經改善'), entry('b','乙組','乙組覺得操作繁複')])
    views['source_segment_ids'] = ['a','b']
    result = generate_report(rows,language='zh',client=Replay(answer([views])))
    assert result.sections[0].blocks[0].options[0].source_segment_ids == ['a','b']
    rows[1].speaker_id = 'other'
    remaining = generate_report(rows,language='zh',client=Replay(answer([views])))
    assert remaining.sections[0].blocks[0].type == 'EditorialText'
    assert remaining.sections[0].blocks[0].source_segment_ids == ['b']


@pytest.mark.parametrize('quote,owner', [('Marin: I will send tiles to Ren.', 'Ren'),
    ('Marin will not send tiles.', 'Marin'), ('Marin could send tiles.', 'Marin')])
def test_recipient_or_suggestion_cannot_become_an_assignment(quote,owner):
    task = item('tasks','Deliver tiles','a',entries=[entry('a',owner,quote)])
    with pytest.raises(ReportGenerationError):
        generate_report([segment('a',quote)],language='en',client=Replay(answer([task])))
