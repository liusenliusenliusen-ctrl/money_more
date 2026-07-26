"""Cursor Agent 作为分析 Provider（读仓库上下文，输出 JSON，不改代码）。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from money_more.llm.providers.base import LLMProvider
from money_more.llm.providers.openai_compat import parse_json_object
from money_more.llm.timeout_util import LLMTimeoutError, run_with_timeout
from money_more.utils.json_util import dumps_json
from money_more.utils.logging_util import setup_logging

log = setup_logging()

CURSOR_ANALYSIS_WRAPPER = """你是 money_more 的独立投研分析 Agent（Cursor）。

## 硬性约束
1. **只做分析，不要修改任何代码或文件**
2. **最终回复必须是单个合法 JSON 对象**（不要 Markdown 代码围栏，不要解释文字）
3. 可阅读 reports/、config.yaml.example、近期 digest 作为背景，但**以本轮用户 payload 为准**
4. **持仓只认 payload 里的 holdings / holdings_basis**：空则按空仓；禁止从历史报告、模拟盘推断「当前持有」
5. 投资取向：中长线，不是短线

## System 角色说明
{system_prompt}

## 用户数据（JSON）
{user_json}

请直接输出符合上述 schema 的 JSON。
"""


class CursorProvider(LLMProvider):
    """通过 cursor-sdk 本地 Agent 做一轮只读分析。"""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "composer-2.5",
        cwd: str | Path | None = None,
        name: str = "cursor",
        timeout_seconds: float = 180.0,
        max_retries: int = 2,
    ) -> None:
        self.name = name
        self.api_key = (api_key or "").strip()
        self.model = model
        self.cwd = str(Path(cwd or Path.cwd()).resolve())
        self._timeout = float(timeout_seconds)
        self._default_max_retries = int(max_retries)

    def available(self) -> tuple[bool, str]:
        if not self.api_key or self.api_key.startswith("your_"):
            return False, f"{self.name}: 未配置 CURSOR_API_KEY"
        try:
            import cursor_sdk  # noqa: F401
        except ImportError:
            return False, f"{self.name}: 未安装 cursor-sdk"
        return True, "ok"

    def _prompt_once(self, prompt: str) -> str:
        from cursor_sdk import Agent, AgentOptions, LocalAgentOptions

        def _call() -> str:
            result = Agent.prompt(
                prompt,
                AgentOptions(
                    api_key=self.api_key,
                    model=self.model,
                    local=LocalAgentOptions(cwd=self.cwd),
                ),
            )
            text = getattr(result, "result", None) or getattr(result, "text", None) or ""
            return str(text)

        return run_with_timeout(_call, self._timeout)

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

        user_json = (
            user_payload if isinstance(user_payload, str) else dumps_json(user_payload, indent=2)
        )
        # Cursor Agent 不吃 temperature；payload 已含完整上下文
        _ = temperature
        prompt = CURSOR_ANALYSIS_WRAPPER.format(
            system_prompt=system_prompt,
            user_json=user_json,
        )
        retries = self._default_max_retries if max_retries is None else int(max_retries)
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                log.info(
                    "CursorProvider analyze attempt=%s/%s model=%s timeout=%.0fs",
                    attempt + 1,
                    retries + 1,
                    self.model,
                    self._timeout,
                )
                text = self._prompt_once(
                    prompt if attempt == 0 else prompt + "\n\n上次输出非法，请只返回 JSON。"
                )
                if not str(text).strip():
                    raise ValueError("Cursor Agent 返回空结果")
                data = parse_json_object(str(text))
                if required_keys:
                    missing = [k for k in required_keys if k not in data]
                    if missing:
                        raise ValueError(f"LLM 输出缺少字段: {missing}")
                return data
            except LLMTimeoutError as exc:
                last_error = exc
                log.warning("CursorProvider timeout attempt=%s: %s", attempt + 1, exc)
            except Exception as exc:
                last_error = exc
                log.warning("CursorProvider failed attempt=%s: %s", attempt + 1, exc)
            if attempt < retries:
                time.sleep(min(8.0, 2**attempt))
        raise RuntimeError(
            f"{self.name} 分析失败（已重试 {retries} 次，timeout={self._timeout:.0f}s）: {last_error}"
        )
