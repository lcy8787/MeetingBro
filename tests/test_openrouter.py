import json
import io
import urllib.error
import pytest
from meetingbro.llm.openai_compatible import (
    OpenAICompatibleClient,
    OpenAICompatibleConfig,
    LLMResponseError,
    _openrouter_reasoning,
    extract_json_object,
)


def client(base_url='https://example.com/v1', model='model', **kwargs):
    return OpenAICompatibleClient(OpenAICompatibleConfig(
        'private-api-key', base_url, model, retry_backoff_seconds=0, **kwargs))


@pytest.mark.parametrize('status,code,calls', [
    (401, 'model_authentication', 1),
    (403, 'model_authentication', 1),
    (400, 'provider_error', 1),
    (429, 'model_rate_limited', 2),
    (500, 'provider_error', 2),
    (503, 'provider_error', 2),
])
def test_http_errors_are_classified_without_exposing_provider_content(monkeypatch, caplog, status, code, calls):
    requests = []
    def request(req, timeout):
        requests.append(req)
        raise urllib.error.HTTPError(req.full_url, status, 'private provider message', {},
                                     io.BytesIO(b'private model response'))
    monkeypatch.setattr('urllib.request.urlopen', request)
    with pytest.raises(LLMResponseError) as failure:
        client().chat(system='summary', user='private transcript', max_tokens=100)
    assert failure.value.code == code and len(requests) == calls
    assert 'private' not in str(failure.value) and 'private' not in caplog.text


@pytest.mark.parametrize('reason,code', [
    (OSError('private network information'), 'model_connection'),
    (TimeoutError('private timeout'), 'model_timeout'),
])
def test_transient_connection_errors_retry_once_then_classify(monkeypatch, reason, code):
    calls = []
    def request(req, timeout):
        calls.append(req)
        raise urllib.error.URLError(reason)
    monkeypatch.setattr('urllib.request.urlopen', request)
    with pytest.raises(LLMResponseError) as failure:
        client().chat(system='summary', user='private transcript', max_tokens=100)
    assert failure.value.code == code and len(calls) == 2
    assert 'private' not in str(failure.value)


def test_retry_budget_is_bounded_and_can_be_disabled(monkeypatch):
    calls = []
    def request(req, timeout):
        calls.append(req)
        raise urllib.error.HTTPError(req.full_url, 503, 'down', {}, io.BytesIO(b''))
    monkeypatch.setattr('urllib.request.urlopen', request)
    with pytest.raises(LLMResponseError) as failure:
        client(max_attempts=1).chat(system='s', user='u', max_tokens=10)
    assert failure.value.code == 'provider_error' and len(calls) == 1


def test_openrouter_endpoint_auth_and_model_are_sent_correctly(monkeypatch):
    monkeypatch.setattr('meetingbro.llm.openai_compatible._openrouter_reasoning_options', lambda: {'test/model': {'mandatory': False, 'supported_efforts': ['high', 'low', 'none']}})
    calls = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b'{"choices":[{"finish_reason":"stop","message":{"content":"result"}}]}'
    def request(req, timeout):
        calls.append(req); return Response()
    monkeypatch.setattr('urllib.request.urlopen', request)
    assert client('https://openrouter.ai/api/v1', 'test/model').chat(
        system='system', user='transcript', max_tokens=10) == 'result'
    assert calls[0].full_url == 'https://openrouter.ai/api/v1/chat/completions'
    assert calls[0].get_header('Authorization') == 'Bearer private-api-key'
    assert json.loads(calls[0].data)['model'] == 'test/model'
    assert json.loads(calls[0].data)['reasoning'] == {'effort': 'none', 'exclude': True}


def mock_response(monkeypatch, payload, *, raw=None):
    requests = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return raw if raw is not None else json.dumps(payload).encode()
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
    ({'choices': [{'finish_reason': 'content_filter', 'message': {'content': 'partial'}}]}, 'incomplete_response'),
])
def test_incomplete_answers_are_never_replaced_by_reasoning_or_silently_retried(monkeypatch, payload, code):
    requests = mock_response(monkeypatch, payload)
    with pytest.raises(LLMResponseError) as error:
        client().chat(system='summary', user='transcript', max_tokens=3200)
    assert error.value.code == code
    assert len(requests) == 1
    assert 'private thinking' not in str(error.value)


def test_finish_reason_length_accepts_complete_json_but_not_truncated_text(monkeypatch):
    complete = json.dumps({'title': 'ok', 'sections': []})
    requests = mock_response(monkeypatch, {'choices': [{'finish_reason': 'length',
        'message': {'content': complete}}]})
    assert client().chat(system='s', user='u', max_tokens=10,
                         response_schema={'type': 'object'}) == complete
    assert len(requests) == 1


def test_non_json_provider_body_is_classified_as_invalid_response(monkeypatch):
    requests = mock_response(monkeypatch, None, raw=b'<html>bad gateway</html>')
    with pytest.raises(LLMResponseError) as error:
        client().chat(system='s', user='u', max_tokens=10)
    assert error.value.code == 'invalid_response' and len(requests) == 1


def test_openrouter_uses_strict_json_schema_and_a_capable_provider(monkeypatch):
    monkeypatch.setattr('meetingbro.llm.openai_compatible._openrouter_reasoning_options', lambda: {'model': None})
    requests = mock_response(monkeypatch, {'choices': [{'finish_reason': 'stop', 'message': {'content': '{}'}}]})
    schema = {'type': 'object', 'properties': {}, 'additionalProperties': False}
    assert client('https://openrouter.ai/api/v1').chat(
        system='summary', user='transcript', max_tokens=4800, response_schema=schema) == '{}'
    body = json.loads(requests[0].data)
    assert body['response_format'] == {'type': 'json_schema', 'json_schema': {'name': 'meeting_report', 'strict': True, 'schema': schema}}
    assert body['provider'] == {'require_parameters': True}
    assert 'reasoning' not in body


def test_unsupported_structured_output_retries_once_without_schema_parameters(monkeypatch):
    monkeypatch.setattr('meetingbro.llm.openai_compatible._openrouter_reasoning_options', lambda: {'model': None})
    requests = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b'{"choices":[{"finish_reason":"stop","message":{"content":"{}"}}]}'
    def request(req, timeout):
        requests.append(json.loads(req.data))
        if len(requests) == 1:
            raise urllib.error.HTTPError(req.full_url, 400, 'unsupported',
                                         {}, io.BytesIO(b'response_format json_schema unsupported'))
        return Response()
    monkeypatch.setattr('urllib.request.urlopen', request)
    schema = {'type': 'object', 'properties': {}, 'additionalProperties': False}
    assert client('https://openrouter.ai/api/v1').chat(
        system='s', user='u', max_tokens=10, response_schema=schema) == '{}'
    assert len(requests) == 2
    assert 'response_format' not in requests[1] and 'provider' not in requests[1]


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


@pytest.mark.parametrize('raw,expected', [
    ('{"a":1}', {'a': 1}),
    ('```json\n{"a":1}\n```', {'a': 1}),
    ('Here is the report:\n{"a":1}\nThanks', {'a': 1}),
    ('prefix {"a":{"b":2}} suffix', {'a': {'b': 2}}),
])
def test_json_extraction_handles_fences_and_surrounding_prose(raw, expected):
    assert extract_json_object(raw) == expected


@pytest.mark.parametrize('raw', ['', 'not json', '{"a":', '{"a":1'])
def test_json_extraction_rejects_truncated_or_missing_objects(raw):
    with pytest.raises(json.JSONDecodeError):
        extract_json_object(raw)


@pytest.mark.parametrize('content', [
    [{'type': 'text', 'text': 'first '}, {'type': 'text', 'text': 'second'}],
    {'text': 'first second'},
])
def test_openai_content_parts_and_dict_shapes_are_read_without_using_reasoning(monkeypatch, content):
    mock_response(monkeypatch, {'choices': [{'finish_reason': 'stop', 'message': {
        'content': content, 'reasoning': 'must not be used'}}]})
    assert client().chat(system='s', user='u', max_tokens=10) == 'first second'
