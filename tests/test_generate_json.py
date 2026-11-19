"""providers.generate_json: a system prompt, a user text and a JSON schema make one JSON answer —
a chain's pass. Gemini gets the schema as response_json_schema, OpenAI-compatible endpoints as a
json_schema response format; the output cap shows as finish ``length``; transient failures are
retried with backoff, anything else is raised at once. No network: the clients are fakes."""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from paperlens import providers  # noqa: E402

SCHEMA = {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]}


class _Gemini:
    def __init__(self, finish="STOP", fail_first: Exception | None = None):
        self.calls, self.finish, self.fail_first = [], finish, fail_first
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, **kw):
        self.calls.append(kw)
        if self.fail_first is not None:
            exc, self.fail_first = self.fail_first, None
            raise exc
        return SimpleNamespace(text=' {"a": 1} ', model_version="gemini-3.1-pro-preview-0101",
                               candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name=self.finish))],
                               usage_metadata=SimpleNamespace(prompt_token_count=10, candidates_token_count=5, total_token_count=15))


class _Chat:
    def __init__(self, finish="stop"):
        self.calls, self.finish = [], finish
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.responses = SimpleNamespace(create=lambda **kw: (_ for _ in ()).throw(AssertionError("not the responses API")))

    def _create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"a": 2}'), finish_reason=self.finish)],
                               model="gpt-4.1-2025-04-14", usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3, total_tokens=10))


def test_gemini_gets_the_system_prompt_the_schema_and_the_cap(monkeypatch) -> None:
    fake = _Gemini(finish="MAX_TOKENS")
    monkeypatch.setattr(providers, "_google_client", lambda key: fake)
    text, finish, usage, served = providers.generate_json("gemini-3.1-pro-preview", "k", system="Be exact.", user="Title: T\n\nbody", schema=SCHEMA, max_tokens=60_000)
    assert text == '{"a": 1}' and finish == "length" and usage == {"prompt": 10, "completion": 5, "total": 15} and served == "gemini-3.1-pro-preview-0101"
    sent = fake.calls[-1]
    assert sent["model"] == "gemini-3.1-pro-preview" and sent["contents"] == "Title: T\n\nbody"
    cfg = sent["config"]
    assert cfg.system_instruction == "Be exact." and cfg.response_mime_type == "application/json" and cfg.response_json_schema == SCHEMA
    assert cfg.max_output_tokens == 60_000 and cfg.temperature == 0


def test_openai_compatible_gets_a_json_schema_response_format(monkeypatch) -> None:
    fake = _Chat(finish="length")
    monkeypatch.setattr(providers, "_openai_compat_client", lambda key, base_url=None: fake)
    text, finish, usage, served = providers.generate_json("gpt-4.1", "k", system="S", user="U", schema=SCHEMA, max_tokens=4000)
    assert text == '{"a": 2}' and finish == "length" and usage["total"] == 10 and served == "gpt-4.1-2025-04-14"
    sent = fake.calls[-1]
    assert sent["messages"] == [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}] and sent["max_tokens"] == 4000
    assert sent["response_format"] == {"type": "json_schema", "json_schema": {"name": "answer", "schema": SCHEMA, "strict": False}}
    text, finish, _u, _s = providers.generate_json("gpt-4.1", "k", system="S", user="U", schema=None, max_tokens=100)
    assert fake.calls[-1]["response_format"] == {"type": "json_object"}


def test_a_transient_failure_is_retried_with_backoff_and_a_bad_request_is_not(monkeypatch) -> None:
    class Overloaded(Exception):
        status_code = 503
    fake = _Gemini(fail_first=Overloaded("try later"))
    monkeypatch.setattr(providers, "_google_client", lambda key: fake)
    slept: list[float] = []
    text, finish, _u, _s = providers.generate_json("gemini-3.1-pro-preview", "k", system="S", user="U", schema=SCHEMA, sleep=slept.append)
    assert text == '{"a": 1}' and finish == "stop" and slept == [5] and len(fake.calls) == 2

    class Bad(Exception):
        status_code = 400
    fake = _Gemini(fail_first=Bad("bad request"))
    monkeypatch.setattr(providers, "_google_client", lambda key: fake)
    with pytest.raises(Bad):
        providers.generate_json("gemini-3.1-pro-preview", "k", system="S", user="U", schema=SCHEMA, sleep=slept.append)
    assert len(fake.calls) == 1 and slept == [5]

    fake = _Gemini(fail_first=Overloaded("still"))
    monkeypatch.setattr(providers, "_google_client", lambda key: fake)
    with pytest.raises(Overloaded):                                   # tries exhausted: the last failure is raised
        providers.generate_json("gemini-3.1-pro-preview", "k", system="S", user="U", schema=SCHEMA, tries=1, sleep=slept.append)
