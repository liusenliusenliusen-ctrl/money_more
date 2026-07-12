"""可插拔 LLM Provider：OpenAI 兼容 / Cursor / Anthropic 等。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LLMProvider(ABC):
    """统一补全接口：输入 system + user，输出 JSON dict。"""

    name: str = "base"

    @abstractmethod
    def complete_json(
        self,
        system_prompt: str,
        user_payload: dict[str, Any] | str,
        *,
        temperature: float = 0.3,
        required_keys: list[str] | None = None,
        max_retries: int = 2,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def available(self) -> tuple[bool, str]:
        return True, "ok"
