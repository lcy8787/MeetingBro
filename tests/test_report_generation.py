import asyncio
import json
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from meetingbro.reports.generator import generate_report
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
        doc = generate_report([segment()], language='en', client=client)
        assert doc.generation_method == 'extractive'
        assert doc.sections[0].blocks[0].text == 'Sales were 12 units'
        assert doc.warnings


def test_long_individual_segment_is_not_truncated():
    text = 'a' * 17000
    doc = generate_report([segment(text)], language='en')
    assert ''.join(section.blocks[0].text for section in doc.sections) == text
    assert len(doc.sections) == 3


def test_report_endpoint_persists_new_revisions_and_handles_missing_transcript(tmp_path, monkeypatch):
    from meetingbro.main import app, OpenAICompatibleClient

    storage = Storage(tmp_path / 'db.sqlite')
    storage.create_meeting('m')
    storage.create_meeting('empty')
    storage.insert_segment(segment())
    app.state.storage = storage
    app.state.report_locks = {}
    monkeypatch.setattr(OpenAICompatibleClient, 'from_env', classmethod(lambda cls: None))

    async def exercise():
        from meetingbro.main import generate_meeting_report, list_reports
        with pytest.raises(HTTPException) as missing:
            await generate_meeting_report('absent')
        assert missing.value.status_code == 404
        with pytest.raises(HTTPException) as empty:
            await generate_meeting_report('empty')
        assert empty.value.status_code == 422
        first, second = await asyncio.gather(generate_meeting_report('m'), generate_meeting_report('m'))
        assert {first.revision, second.revision} == {1, 2}
        listing = await list_reports('m')
        assert [r.revision for r in listing] == [1, 2]
        assert listing[0].document.sections[0].blocks[0].source_segment_ids == ['s']
    asyncio.run(exercise())
    storage.close()
