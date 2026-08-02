"""OpenAICompatProvider：空 content 不得伪装成 {}。"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest

from money_more.llm.providers.openai_compat import EmptyLLMContentError, OpenAICompatProvider


class _FakeCompletions:
    def __init__(self, contents: list[str]) -> None:
        self._contents = list(contents)
        self.calls = 0
        self.last_kwargs: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> Any:
        self.calls += 1
        self.last_kwargs = kwargs
        content = self._contents.pop(0) if self._contents else ""
        msg = SimpleNamespace(content=content)
        choice = SimpleNamespace(message=msg, finish_reason="stop")
        usage = SimpleNamespace(
            completion_tokens_details=SimpleNamespace(reasoning_tokens=1200)
        )
        return SimpleNamespace(choices=[choice], usage=usage)


class _FakeClient:
    def __init__(self, contents: list[str]) -> None:
        self.chat = SimpleNamespace(completions=_FakeCompletions(contents))


def test_empty_content_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OpenAICompatProvider(
        name="deepseek",
        api_key="sk-test",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-pro",
        timeout=5.0,
        max_retries=2,
        max_tokens=8192,
    )
    fake = _FakeClient(["", '{"executive_summary":"ok","sentiment_temperature":"neutral"}'])
    monkeypatch.setattr(provider, "_client_or_raise", lambda: fake)
    monkeypatch.setattr("money_more.llm.providers.openai_compat.time.sleep", lambda *_: None)

    out = provider.complete_json(
        "sys json",
        {"x": 1},
        required_keys=["executive_summary", "sentiment_temperature"],
    )
    assert out["executive_summary"] == "ok"
    assert fake.chat.completions.calls == 2
    assert fake.chat.completions.last_kwargs.get("max_tokens") == 8192
    assert "重试提醒" in fake.chat.completions.last_kwargs["messages"][1]["content"]


def test_empty_content_all_retries_fail_message(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OpenAICompatProvider(
        name="deepseek",
        api_key="sk-test",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-pro",
        timeout=5.0,
        max_retries=1,
    )
    fake = _FakeClient(["", ""])
    monkeypatch.setattr(provider, "_client_or_raise", lambda: fake)
    monkeypatch.setattr("money_more.llm.providers.openai_compat.time.sleep", lambda *_: None)

    with pytest.raises(RuntimeError) as ei:
        provider.complete_json("sys json", {"x": 1})
    msg = str(ei.value)
    assert "message.content 为空" in msg
    assert "缺少字段" not in msg
    assert isinstance(ei.value.__cause__, EmptyLLMContentError) or "空" in msg


def test_timeout_log_includes_wall_clock(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    provider = OpenAICompatProvider(
        name="deepseek",
        api_key="sk-test",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-pro",
        timeout=1.0,
        max_retries=0,
    )

    class _Slow:
        def create(self, **kwargs: Any) -> Any:
            time.sleep(0.05)
            raise TimeoutError("Request timed out.")

    fake = SimpleNamespace(chat=SimpleNamespace(completions=_Slow()))
    monkeypatch.setattr(provider, "_client_or_raise", lambda: fake)
    monkeypatch.setattr("money_more.llm.providers.openai_compat.time.sleep", lambda *_: None)

    with caplog.at_level(logging.WARNING, logger="money_more"):
        with pytest.raises(RuntimeError):
            provider.complete_json("sys json", {"x": 1})
    joined = " ".join(r.message for r in caplog.records)
    assert "wall=" in joined
    assert "timeout=1" in joined
