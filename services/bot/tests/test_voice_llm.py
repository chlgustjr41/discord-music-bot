"""Interpreter contract. The HTTP client is injected; tests never hit OpenAI."""

import json

import pytest

from jacky.api.voice_actions import Action


class _Resp:
    def __init__(self, status, payload):
        self.status, self._payload = status, payload

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Http:
    def __init__(self, resp):
        self._resp, self.calls = resp, []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._resp


def _completion(actions):
    return {"choices": [{"message": {"content": json.dumps({"actions": actions})}}]}


async def test_interprets_a_multi_command_utterance_in_order():
    from jacky.api.voice_llm import LlmIntentInterpreter

    http = _Http(_Resp(200, _completion([
        {"action": "skip"},
        {"action": "play", "query": "creep", "placement": "end"},
    ])))
    got = await LlmIntentInterpreter(http, "sk", "gpt-4o-mini").interpret("skip then add creep")
    assert got == [Action("skip", count=1), Action("play", query="creep", placement="end")]


async def test_request_constrains_the_model_with_the_strict_schema():
    from jacky.api.voice_actions import ACTION_SCHEMA
    from jacky.api.voice_llm import LlmIntentInterpreter

    http = _Http(_Resp(200, _completion([{"action": "pause"}])))
    await LlmIntentInterpreter(http, "sk-test", "gpt-4o-mini").interpret("pause")
    _url, kwargs = http.calls[0]
    body = kwargs["json"]
    assert kwargs["headers"]["Authorization"] == "Bearer sk-test"
    fmt = body["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"] == ACTION_SCHEMA


async def test_model_output_still_goes_through_validation():
    """Even inside a 200 response, an unknown verb must not survive."""
    from jacky.api.voice_llm import LlmIntentInterpreter

    http = _Http(_Resp(200, _completion([
        {"action": "delete_playlist", "name": "chill"},
        {"action": "pause"},
    ])))
    got = await LlmIntentInterpreter(http, "sk", "m").interpret("whatever")
    assert got == [Action("pause")]


@pytest.mark.parametrize(
    "payload",
    [
        {"choices": [{"message": {"content": "not json"}}]},
        {"choices": []},
        {},
        {"choices": [{"message": {"content": json.dumps({"actions": []})}}]},
    ],
)
async def test_unusable_responses_raise_interpret_error(payload):
    from jacky.api.voice_llm import InterpretError, LlmIntentInterpreter

    with pytest.raises(InterpretError):
        await LlmIntentInterpreter(_Http(_Resp(200, payload)), "sk", "m").interpret("x")


async def test_non_200_raises_interpret_error():
    from jacky.api.voice_llm import InterpretError, LlmIntentInterpreter

    with pytest.raises(InterpretError):
        await LlmIntentInterpreter(_Http(_Resp(500, {})), "sk", "m").interpret("x")


async def test_status_error_is_not_double_wrapped():
    """The non-200 InterpretError must propagate as-is, not be re-caught by the
    broad network handler — otherwise __cause__ would be an InterpretError and
    the message would nest. Mirrors the same guard on OpenAITranscriber."""
    from jacky.api.voice_llm import InterpretError, LlmIntentInterpreter

    with pytest.raises(InterpretError) as exc:
        await LlmIntentInterpreter(_Http(_Resp(500, {})), "sk", "m").interpret("x")
    assert exc.value.__cause__ is None
    assert "500" in str(exc.value)


async def test_network_fault_is_wrapped():
    from jacky.api.voice_llm import InterpretError, LlmIntentInterpreter

    class _Boom:
        def post(self, *a, **k):
            raise OSError("connection reset")

    with pytest.raises(InterpretError) as exc:
        await LlmIntentInterpreter(_Boom(), "sk", "m").interpret("x")
    assert isinstance(exc.value.__cause__, OSError)


class _BoomHttp:
    """Transport fault carrying what aiohttp would: host and URL, no body."""

    def post(self, *_a, **_k):
        raise OSError(
            "Cannot connect to host api.openai.com:443 ssl:default"
        )


@pytest.mark.parametrize("http", [
    _Http(_Resp(500, {})),                                    # non-200
    _BoomHttp(),                                              # transport fault
    _Http(_Resp(200, {"choices": [{"message": {"content": "not json"}}]})),
    _Http(_Resp(200, _completion([]))),                       # no usable actions
])
async def test_interpreter_errors_never_carry_the_transcript(http):
    """Transcripts must not reach stdout, and control.py logs this exception's
    message — so every failure mode must be transcript-free at the SOURCE.
    The transcript travels in the POST body, which no aiohttp error echoes.
    Parametrized over all four: a new failure path must be checked here too."""
    from jacky.api.voice_llm import InterpretError, LlmIntentInterpreter

    secret = "play my extremely private playlist name"
    with pytest.raises(InterpretError) as exc:
        await LlmIntentInterpreter(http, "sk", "m").interpret(secret)
    assert secret not in str(exc.value)
    assert secret not in str(exc.value.__cause__ or "")
