"""Anthropic Claude Provider（官方 SDK 或 OpenAI 兼容网关）。"""

from __future__ import annotations

import time
from typing import Any

from money_more.llm.providers.base import LLMProvider
from money_more.llm.providers.openai_compat import OpenAICompatProvider, parse_json_object
from money_more.utils.json_util import dumps_json


class AnthropicProvider(LLMProvider):
    """优先 anthropic SDK；失败则回退到 base_url 的 OpenAI 兼容网关。"""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "claude-sonnet-4-20250514",
        base_url: str | None = None,
        timeout: float = 90.0,
        max_retries: int = 2,
        max_tokens: int = 32768,
        name: str = "claude",
    ) -> None:
        self.name = name
        self.api_key = (api_key or "").strip()
        self.model = model
        self.base_url = (base_url or "").strip() or None
        self._timeout = timeout
        self._default_max_retries = int(max_retries)
        self._max_tokens = int(max_tokens)
        self._compat: OpenAICompatProvider | None = None

    def available(self) -> tuple[bool, str]:
        if not self.api_key or self.api_key.startswith("your_"):
            return False, f"{self.name}: 未配置 ANTHROPIC_API_KEY / CLAUDE_API_KEY"
        return True, "ok"

    def complete_json(
        self,
        system_prompt: str,
        user_payload: dict[str, Any] | str,
        *,
        temperature: float = 0.3,
        required_keys: list[str] | None = None,
        max_retries: int | None = None,
    ) -> dict[str, Any]:
        ok, reason = self.available()
        if not ok:
            raise ValueError(reason)

        retries = self._default_max_retries if max_retries is None else int(max_retries)

        # OpenAI 兼容网关（如某些代理）
        if self.base_url:
            if self._compat is None:
                self._compat = OpenAICompatProvider(
                    name=self.name,
                    api_key=self.api_key,
                    base_url=self.base_url,
                    model=self.model,
                    timeout=self._timeout,
                    max_retries=retries,
                    max_tokens=self._max_tokens,
                )
            return self._compat.complete_json(
                system_prompt,
                user_payload,
                temperature=temperature,
                required_keys=required_keys,
                max_retries=retries,
            )

        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError(
                "未安装 anthropic，请 pip install anthropic，或配置 CLAUDE_BASE_URL 走兼容网关"
            ) from exc

        client = anthropic.Anthropic(api_key=self.api_key, timeout=self._timeout)
        user_content = (
            user_payload if isinstance(user_payload, str) else dumps_json(user_payload, indent=2)
        )
        last_error: Exception | None = None
        payload_text = user_content
        for attempt in range(retries + 1):
            try:
                msg = client.messages.create(
                    model=self.model,
                    max_tokens=self._max_tokens,
                    temperature=temperature,
                    system=system_prompt + "\n\n请只输出合法 JSON 对象，不要 Markdown 围栏。",
                    messages=[{"role": "user", "content": payload_text}],
                )
                parts: list[str] = []
                for block in msg.content:
                    text = getattr(block, "text", None)
                    if text:
                        parts.append(text)
                content = "\n".join(parts) or "{}"
                data = parse_json_object(content)
                if required_keys:
                    missing = [k for k in required_keys if k not in data]
                    if missing:
                        raise ValueError(f"LLM 输出缺少字段: {missing}")
                return data
            except Exception as exc:
                last_error = exc
                if attempt >= retries:
                    break
                time.sleep(min(8.0, 2**attempt))
                payload_text = dumps_json(
                    {
                        "original_request": user_payload,
                        "previous_error": str(exc),
                        "instruction": "请严格输出 JSON，补全缺失字段。",
                    },
                    indent=2,
                )
        raise RuntimeError(
            f"{self.name} 分析失败（已重试 {retries} 次，timeout={self._timeout:.0f}s）: {last_error}"
        )
