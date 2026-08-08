"""OpenAI 兼容 Provider（DeepSeek / 其它 OpenAI-compatible）。"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from openai import OpenAI

from money_more.llm.providers.base import LLMProvider
from money_more.utils.json_util import dumps_json
from money_more.utils.logging_util import setup_logging

log = setup_logging()


class EmptyLLMContentError(ValueError):
    """API 返回 HTTP 成功但 message.content 为空（V4 JSON+thinking 已知偶发）。"""


class LengthTruncatedError(ValueError):
    """finish_reason=length：输出被 max_tokens 截断，JSON 多半不完整。

    第五波 C8：显式检测并触发「压缩 payload 重试」，而不是等 parse 失败后原样重试。
    """


def _compact_user_payload(payload_text: str, *, limit: int = 14000) -> str:
    """截断时压缩 user payload：过长字符串字段截断，保留结构。

    仅在 finish=length 重试时调用；原始 payload 不做持久修改。
    """
    try:
        obj = json.loads(payload_text)
    except Exception:
        return payload_text[:limit]

    def _trim(x: Any, depth: int = 0) -> Any:
        if isinstance(x, str):
            return x if len(x) <= 1200 else x[:1200] + f"…[截断{len(x) - 1200}字]"
        if isinstance(x, list):
            # 列表过长：保留前 8 项
            items = [_trim(v, depth + 1) for v in x[:8]]
            if len(x) > 8:
                items.append(f"…[省略{len(x) - 8}项]")
            return items
        if isinstance(x, dict):
            return {k: _trim(v, depth + 1) for k, v in x.items()}
        return x

    compact = _trim(obj)
    text = dumps_json(compact, indent=1)
    if len(text) > limit:
        text = text[:limit] + "\n…[payload 已截断以适配 max_tokens]"
    return text


class OpenAICompatProvider(LLMProvider):
    def __init__(
        self,
        *,
        name: str,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 300.0,
        max_retries: int = 2,
        max_tokens: int = 32768,
    ) -> None:
        self.name = name
        self.api_key = (api_key or "").strip()
        self.base_url = base_url
        self.model = model
        self._client: OpenAI | None = None
        self._timeout = timeout
        self._default_max_retries = int(max_retries)
        # thinking 会占用 completion 额度；过小易 finish=length 截断 JSON
        self._max_tokens = int(max_tokens)
        # 第五波 C1：截断/降级统计（进程内累计，供 DQ 与报告趋势）
        self.stats: dict[str, int] = {
            "calls": 0,
            "finish_length": 0,
            "empty_content": 0,
            "compact_retries": 0,
        }

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
        max_retries: int | None = None,
    ) -> dict[str, Any]:
        client = self._client_or_raise()
        retries = self._default_max_retries if max_retries is None else int(max_retries)
        user_content = (
            user_payload if isinstance(user_payload, str) else dumps_json(user_payload, indent=2)
        )
        last_error: Exception | None = None
        payload_text = user_content
        self.stats["calls"] += 1
        for attempt in range(retries + 1):
            t0 = time.monotonic()
            try:
                response = client.chat.completions.create(
                    model=self.model,
                    temperature=temperature,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": payload_text},
                    ],
                    response_format={"type": "json_object"},
                    max_tokens=self._max_tokens,
                )
                elapsed = time.monotonic() - t0
                choice = response.choices[0]
                msg = choice.message
                content = (msg.content or "").strip()
                usage = getattr(response, "usage", None)
                reasoning_tokens = None
                details = getattr(usage, "completion_tokens_details", None) if usage else None
                if details is not None:
                    reasoning_tokens = getattr(details, "reasoning_tokens", None)
                log.info(
                    "%s complete_json ok attempt=%s/%s wall=%.1fs timeout=%.0fs "
                    "max_tokens=%s finish=%s content_len=%s reasoning_tokens=%s",
                    self.name,
                    attempt + 1,
                    retries + 1,
                    elapsed,
                    self._timeout,
                    self._max_tokens,
                    choice.finish_reason,
                    len(content),
                    reasoning_tokens,
                )
                if not content:
                    # 不把空串伪装成 {}，否则会被误报成「缺少全部字段」
                    raise EmptyLLMContentError(
                        "message.content 为空"
                        f"（finish={choice.finish_reason}"
                        + (f", reasoning_tokens={reasoning_tokens}" if reasoning_tokens is not None else "")
                        + f", wall={elapsed:.1f}s"
                        + "）。DeepSeek JSON 模式偶发空 content；将重试。"
                    )
                try:
                    data = parse_json_object(content)
                except json.JSONDecodeError:
                    # C8：截断导致的 JSON 不完整单独归类，重试时压缩 payload
                    if str(choice.finish_reason or "").lower() == "length":
                        self.stats["finish_length"] += 1
                        raise LengthTruncatedError(
                            f"finish=length 输出截断（content_len={len(content)}，"
                            f"max_tokens={self._max_tokens}）→ JSON 不完整"
                        )
                    raise
                if required_keys:
                    missing = [k for k in required_keys if k not in data]
                    if missing:
                        # finish=length 但 JSON 恰好完整、只缺字段：按截断处理
                        if str(choice.finish_reason or "").lower() == "length":
                            self.stats["finish_length"] += 1
                            raise LengthTruncatedError(
                                f"finish=length 且缺字段 {missing} → 按截断重试"
                            )
                        raise ValueError(f"LLM 输出缺少字段: {missing}")
                return data
            except Exception as exc:
                elapsed = time.monotonic() - t0
                last_error = exc
                sleep_hint = ""
                # 墙钟远超配置超时：常见于本机休眠/代理挂起，而非 API 正常慢
                if elapsed > self._timeout * 1.5 + 5:
                    sleep_hint = (
                        f"；wall={elapsed:.1f}s>>timeout={self._timeout:.0f}s"
                        "（疑似本机休眠或代理挂起）"
                    )
                else:
                    sleep_hint = f"；wall={elapsed:.1f}s timeout={self._timeout:.0f}s"
                log.warning(
                    "%s complete_json attempt=%s/%s%s: %s",
                    self.name,
                    attempt + 1,
                    retries + 1,
                    sleep_hint,
                    exc,
                )
                if attempt >= retries:
                    break
                time.sleep(min(8.0, 2**attempt))
                # 空 content：保留原请求，只追加短提醒（避免再嵌套放大 payload）
                if isinstance(exc, EmptyLLMContentError):
                    self.stats["empty_content"] += 1
                    payload_text = (
                        user_content
                        + "\n\n【重试提醒】上一次 API 返回了空 content。"
                        "请在最终答案中输出完整 JSON 对象（含全部必填顶层字段），"
                        "不要只把结论留在思考过程里。"
                    )
                elif isinstance(exc, LengthTruncatedError):
                    self.stats["compact_retries"] += 1
                    # C8：压缩 payload 再试（不再嵌套 original_request+previous_error 放大）
                    payload_text = (
                        _compact_user_payload(user_content)
                        + "\n\n【重试提醒】上一次输出因 max_tokens 截断（finish=length）。"
                        "请精简叙述字段（rationale 等每条≤200字），优先保证 JSON 结构完整、"
                        "必填字段齐全。"
                    )
                    log.warning(
                        "%s finish=length → 压缩 payload 重试（payload %s→%s 字）",
                        self.name,
                        len(user_content),
                        len(payload_text),
                    )
                else:
                    payload_text = dumps_json(
                        {
                            "original_request": user_payload,
                            "previous_error": str(exc),
                            "instruction": (
                                "请严格按 system prompt 的 JSON schema 重新输出一个完整 json 对象，"
                                "补全缺失字段；仅输出 JSON，不要 markdown。"
                            ),
                        },
                        indent=2,
                    )
        raise RuntimeError(
            f"{self.name} 分析失败（已重试 {retries} 次，timeout={self._timeout:.0f}s）: {last_error}"
        ) from last_error


def parse_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise
