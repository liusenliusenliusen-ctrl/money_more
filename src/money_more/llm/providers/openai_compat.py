"""OpenAI 兼容 Provider（DeepSeek / 其它 OpenAI-compatible）。"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from openai import OpenAI

from money_more.llm.providers.base import LLMProvider
from money_more.utils.json_util import dumps_json


class OpenAICompatProvider(LLMProvider):
    def __init__(
        self,
        *,
        name: str,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 120.0,
    ) -> None:
        self.name = name
        self.api_key = (api_key or "").strip()
        self.base_url = base_url
        self.model = model
        self._client: OpenAI | None = None
        self._timeout = timeout

    def available(self) -> tuple[bool, str]:
        if not self.api_key or self.api_key.startswith("your_"):
            return False, f"{self.name}: 未配置 API Key"
        return True, "ok"

    def _client_or_raise(self) -> OpenAI:
        ok, reason = self.available()
        if not ok:
            raise ValueError(reason)
        if self._client is None:
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self._timeout,
                max_retries=0,
            )
        return self._client

    def complete_json(
        self,
        system_prompt: str,
        user_payload: dict[str, Any] | str,
        *,
        temperature: float = 0.3,
        required_keys: list[str] | None = None,
        max_retries: int = 2,
    ) -> dict[str, Any]:
        client = self._client_or_raise()
        user_content = (
            user_payload if isinstance(user_payload, str) else dumps_json(user_payload, indent=2)
        )
        last_error: Exception | None = None
        payload_text = user_content
        for attempt in range(max_retries + 1):
            try:
                response = client.chat.completions.create(
                    model=self.model,
                    temperature=temperature,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": payload_text},
                    ],
                    response_format={"type": "json_object"},
                )
                content = response.choices[0].message.content or "{}"
                data = parse_json_object(content)
                if required_keys:
                    missing = [k for k in required_keys if k not in data]
                    if missing:
                        raise ValueError(f"LLM 输出缺少字段: {missing}")
                return data
            except Exception as exc:
                last_error = exc
                if attempt >= max_retries:
                    break
                time.sleep(min(8.0, 2**attempt))
                payload_text = dumps_json(
                    {
                        "original_request": user_payload,
                        "previous_error": str(exc),
                        "instruction": "请严格按 system prompt 的 JSON schema 重新输出，补全缺失字段。",
                    },
                    indent=2,
                )
        raise RuntimeError(f"{self.name} 分析失败（已重试 {max_retries} 次）: {last_error}")


def parse_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise
