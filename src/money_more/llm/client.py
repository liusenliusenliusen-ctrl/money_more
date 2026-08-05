from __future__ import annotations

from typing import Any

from money_more.config import AppConfig
from money_more.llm.providers.base import LLMProvider
from money_more.llm.providers.openai_compat import OpenAICompatProvider
from money_more.llm.prompts import (
    DECISION_SECONDARY_SYSTEM,
    DECISION_SYSTEM,
    INTELLIGENCE_DIGEST_SYSTEM,
    MARKET_SYSTEM,
    REVIEW_SYSTEM,
    SECTOR_SYSTEM,
    STOCK_SYSTEM,
)

__all__ = [
    "LLMClient",
    "MARKET_SYSTEM",
    "SECTOR_SYSTEM",
    "STOCK_SYSTEM",
    "DECISION_SYSTEM",
    "DECISION_SECONDARY_SYSTEM",
    "REVIEW_SYSTEM",
    "INTELLIGENCE_DIGEST_SYSTEM",
]


class LLMClient:
    """兼容旧调用；底层可换成任意 LLMProvider。"""

    def __init__(
        self,
        config: AppConfig,
        timeout: float | None = None,
        provider: LLMProvider | None = None,
    ) -> None:
        if not provider and not (config.llm_api_key or "").strip():
            raise ValueError("未设置 LLM_API_KEY，请在 .env 中配置")
        self.config = config
        agents = getattr(config, "agents", None)
        resolved_timeout = float(
            timeout
            if timeout is not None
            else (getattr(agents, "llm_timeout_seconds", 300) or 300)
        )
        resolved_retries = int(getattr(agents, "llm_max_retries", 2) or 2)
        self.provider = provider or OpenAICompatProvider(
            name="deepseek",
            api_key=config.llm_api_key,
            base_url=config.llm_base_url,
            model=config.llm_model,
            timeout=resolved_timeout,
            max_retries=resolved_retries,
        )
        ok, reason = self.provider.available()
        if not ok:
            raise ValueError(reason)
        self.model = getattr(self.provider, "model", config.llm_model)
        self._default_max_retries = resolved_retries

    def analyze_json(
        self,
        system_prompt: str,
        user_payload: dict[str, Any],
        temperature: float = 0.3,
        required_keys: list[str] | None = None,
        max_retries: int | None = None,
    ) -> dict[str, Any]:
        retries = self._default_max_retries if max_retries is None else int(max_retries)
        return self.provider.complete_json(
            system_prompt,
            user_payload,
            temperature=temperature,
            required_keys=required_keys,
            max_retries=retries,
        )
