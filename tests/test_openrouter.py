import json
import pytest
from meetingbro.llm.openai_compatible import OpenAICompatibleClient, OpenAICompatibleConfig, LLMResponseError, _openrouter_reasoning


def test_openrouter_endpoint_auth_and_model_are_sent_correctly(monkeypatch):
    monkeypatch.setattr('meetingbro.llm.openai_compatible._openrouter_reasoning_options', lambda: {'test/model': {'mandatory': False, 'supported_efforts': ['high', 'low', 'none']}})
    calls = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b'{"choices":[{"message":{"content":"result"}}]}'
    def request(req, timeout):
        calls.append(req); return Response()
    monkeypatch.setattr('urllib.request.urlopen', request)
    client = OpenAICompatibleClient(OpenAICompatibleConfig('test-key', 'https://openrouter.ai/api/v1', 'test/model'))
    assert client.chat(system='system',user='transcript',max_tokens=10) == 'result'
    assert calls[0].full_url == 'https://openrouter.ai/api/v1/chat/completions'
    assert calls[0].get_header('Authorization') == 'Bearer test-key'
    assert json.loads(calls[0].data)['model'] == 'test/model'
    assert json.loads(calls[0].data)['reasoning'] == {'effort': 'none', 'exclude': True}


def mock_response(monkeypatch, payload):
    requests = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return json.dumps(payload).encode()
    def request(req, timeout):
        requests.append(req)
        return Response()
    monkeypatch.setattr('urllib.request.urlopen', request)
    return requests


@pytest.mark.parametrize('payload,code', [
    ({'choices': [{'finish_reason': 'length', 'message': {'content': '', 'reasoning': 'private thinking'}}]}, 'output_truncated'),
    ({'choices': [{'finish_reason': 'length', 'message': {'content': '{"blocks":['}}]}, 'output_truncated'),
    ({'choices': [{'finish_reason': 'stop', 'message': {'content': '', 'reasoning': '{"blocks":[]}'}}]}, 'empty_response'),
    ({'choices': []}, 'empty_response'),
    ({'error': {'message': 'upstream error'}}, 'provider_error'),
    ({'choices': [{'message': {'refusal': 'no'}}]}, 'refusal'),
])
def test_incomplete_answers_are_never_replaced_by_reasoning_or_silently_retried(monkeypatch, payload, code):
    requests = mock_response(monkeypatch, payload)
    client = OpenAICompatibleClient(OpenAICompatibleConfig('test-key', 'https://example.com/v1', 'model'))
    with pytest.raises(LLMResponseError) as error:
        client.chat(system='summary', user='transcript', max_tokens=3200)
    assert error.value.code == code
    assert len(requests) == 1
    assert 'private thinking' not in str(error.value)


def test_openrouter_uses_strict_json_schema_and_a_capable_provider(monkeypatch):
    monkeypatch.setattr('meetingbro.llm.openai_compatible._openrouter_reasoning_options', lambda: {'model': None})
    requests = mock_response(monkeypatch, {'choices': [{'finish_reason': 'stop', 'message': {'content': '{}'}}]})
    schema = {'type': 'object', 'properties': {}, 'additionalProperties': False}
    client = OpenAICompatibleClient(OpenAICompatibleConfig('test-key', 'https://openrouter.ai/api/v1', 'model'))
    assert client.chat(system='summary', user='transcript', max_tokens=4800, response_schema=schema) == '{}'
    body = json.loads(requests[0].data)
    assert body['response_format'] == {'type': 'json_schema', 'json_schema': {'name': 'meeting_report', 'strict': True, 'schema': schema}}
    assert body['provider'] == {'require_parameters': True}
    assert 'reasoning' not in body


def test_mandatory_reasoning_is_reduced_but_never_disabled(monkeypatch):
    monkeypatch.setattr('meetingbro.llm.openai_compatible._openrouter_reasoning_options', lambda: {'model': {'mandatory': True, 'supported_efforts': ['high', 'low']}})
    assert _openrouter_reasoning('model') == {'effort': 'low', 'exclude': True}


def test_connection_check_rejects_empty_model_answer(monkeypatch):
    import asyncio
    from meetingbro import main
    def chat(*args, **kwargs):
        assert kwargs['max_tokens'] >= 256
        raise LLMResponseError('empty_response', 'No final answer')
    monkeypatch.setattr(main.OpenAICompatibleClient, 'chat', chat)
    result = asyncio.run(main.test_llm(main.TestLlmRequest(api_key='test-key', base_url='https://openrouter.ai/api/v1', model='model')))
    assert result['ok'] is False
