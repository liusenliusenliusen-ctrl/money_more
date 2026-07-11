from __future__ import annotations

import json
import re
import time
from typing import Any

from openai import OpenAI

from money_more.config import AppConfig
from money_more.utils.json_util import dumps_json
from money_more.llm.prompts import (
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
    "REVIEW_SYSTEM",
    "INTELLIGENCE_DIGEST_SYSTEM",
]


class LLMClient:
    def __init__(self, config: AppConfig, timeout: float = 120.0) -> None:
        if not config.llm_api_key:
            raise ValueError("未设置 LLM_API_KEY，请在 .env 中配置")
        self.config = config
        self.client = OpenAI(
            api_key=config.llm_api_key,
            base_url=config.llm_base_url,
            timeout=timeout,
            max_retries=0,  # 由本层控制重试与退避
        )
        self.model = config.llm_model

    def analyze_json(
        self,
        system_prompt: str,
        user_payload: dict[str, Any],
        temperature: float = 0.3,
        required_keys: list[str] | None = None,
        max_retries: int = 2,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        payload = user_payload
        for attempt in range(max_retries + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    temperature=temperature,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {
                            "role": "user",
                            "content": dumps_json(payload, indent=2),
                        },
                    ],
                    response_format={"type": "json_object"},
                )
                content = response.choices[0].message.content or "{}"
                data = self._parse_json(content)
                if required_keys:
                    missing = [k for k in required_keys if k not in data]
                    if missing:
                        raise ValueError(f"LLM 输出缺少字段: {missing}")
                return data
            except Exception as exc:
                last_error = exc
                if attempt >= max_retries:
                    break
                # 指数退避：1s, 2s, ...
                time.sleep(min(8.0, 2**attempt))
                payload = {
                    "original_request": user_payload,
                    "previous_error": str(exc),
                    "instruction": "请严格按 system prompt 的 JSON schema 重新输出，补全缺失字段。",
                }
        raise RuntimeError(f"LLM 分析失败（已重试 {max_retries} 次）: {last_error}")

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                return json.loads(match.group())
            raise
