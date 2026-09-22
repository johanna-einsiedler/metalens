"""OpenAI's Pro models are called through the Responses API; everything around them (prompts,
image blocks, usage, the served snapshot) is reported exactly like a chat completion."""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from paperlens import providers  # noqa: E402


class _Fake:
    """An OpenAI client whose chat endpoint refuses Pro models, like the real one."""
    def __init__(self):
        self.calls = []
        self.responses = SimpleNamespace(create=self._create)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._chat))

    def _chat(self, **kw):
        raise AssertionError("chat/completions must not be called for a Pro model")

    def _create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(output_text="  {\"a\": 1}  ", status="incomplete", model="gpt-5.5-pro-2026-04-23",
                               incomplete_details=SimpleNamespace(reason="max_output_tokens"),
                               usage=SimpleNamespace(input_tokens=120, output_tokens=30, total_tokens=150))


def test_pro_models_go_through_the_responses_api(monkeypatch):
    fake = _Fake()
    monkeypatch.setattr(providers, "_openai_compat_client", lambda key, base_url=None: fake)
    assert providers.uses_responses_api("gpt-5.5-pro", "openai") and providers.uses_responses_api("o3-pro", "openai")
    assert not providers.uses_responses_api("gpt-5.5", "openai") and not providers.uses_responses_api("gemini-2.5-pro", "google")

    blocks = [{"type": "text", "text": "Read the table."}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA", "detail": "high"}}]
    text, finish, usage, served = providers.extract_with_images("gpt-5.5-pro", "k", blocks, ["AAA"], "Read the table.", "", 1)
    assert text == '{"a": 1}' and finish == "length" and usage == {"prompt": 120, "completion": 30, "total": 150}
    assert served == "gpt-5.5-pro-2026-04-23"
    sent = fake.calls[-1]
    assert sent["model"] == "gpt-5.5-pro" and sent["input"] == [{"role": "user", "content": [
        {"type": "input_text", "text": "Read the table."},
        {"type": "input_image", "image_url": "data:image/png;base64,AAA", "detail": "high"}]}]

    text, finish, usage, served = providers.extract_with_text("gpt-5.5-pro", "k", "# Paper\n…", "Extract.", "")
    assert text == '{"a": 1}' and fake.calls[-1]["input"].startswith("Extract.")
    assert providers.generate_text("gpt-5.5-pro", "k", "ping", max_tokens=64) == '{"a": 1}' and fake.calls[-1]["max_output_tokens"] == 64
