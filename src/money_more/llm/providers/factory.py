"""根据配置构造 Provider。"""

from __future__ import annotations

from typing import Any

from money_more.config import AppConfig
from money_more.llm.providers.anthropic_provider import AnthropicProvider
from money_more.llm.providers.base import LLMProvider
from money_more.llm.providers.cursor_provider import CursorProvider
from money_more.llm.providers.openai_compat import OpenAICompatProvider


def build_provider(
    kind: str,
    *,
    config: AppConfig,
    name: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> LLMProvider:
    kind = (kind or "openai").strip().lower()
    label = name or kind
    agents = getattr(config, "agents", None)
    llm_timeout = float(getattr(agents, "llm_timeout_seconds", 300) or 300)
    llm_retries = int(getattr(agents, "llm_max_retries", 2) or 2)
    llm_max_tokens = int(getattr(agents, "llm_max_tokens", 32768) or 32768)
    cursor_timeout = float(getattr(agents, "cursor_timeout_seconds", 180) or 180)
    cursor_retries = int(getattr(agents, "cursor_max_retries", 2) or 2)

    if kind in ("openai", "openai_compat", "deepseek", "llm"):
        return OpenAICompatProvider(
            name=label,
            api_key=api_key or config.llm_api_key,
            base_url=base_url or config.llm_base_url,
            model=model or config.llm_model,
            timeout=llm_timeout,
            max_retries=llm_retries,
            max_tokens=llm_max_tokens,
        )

    if kind in ("cursor", "cursor_agent"):
        return CursorProvider(
            name=label,
            api_key=api_key or config.cursor_api_key,
            model=model or config.agents.cursor_model,
            cwd=config.project_root,
            timeout_seconds=cursor_timeout,
            max_retries=cursor_retries,
        )

    if kind in ("claude", "anthropic"):
        return AnthropicProvider(
            name=label,
            api_key=api_key or config.claude_api_key,
            model=model or config.claude_model,
            base_url=base_url or (config.claude_base_url or None),
            timeout=llm_timeout,
            max_retries=llm_retries,
            max_tokens=llm_max_tokens,
        )

    raise ValueError(f"未知 provider 类型: {kind}")


def build_providers_from_config(config: AppConfig) -> dict[str, LLMProvider]:
    """返回 role -> provider。缺密钥的 provider 不会放入。"""
    out: dict[str, LLMProvider] = {}
    specs: list[dict[str, Any]] = [
        {
            "role": "primary",
            "kind": config.agents.primary_provider,
            "name": "deepseek",
            "model": config.agents.primary_model or config.llm_model,
        },
        {
            "role": "secondary",
            "kind": config.agents.secondary_provider,
            "name": config.agents.secondary_provider,
            "model": config.agents.secondary_model,
        },
        {
            "role": "synthesizer",
            "kind": config.agents.synthesizer_provider,
            "name": "synthesizer",
            "model": config.agents.synthesizer_model or config.llm_model,
        },
    ]
    for spec in specs:
        kind = str(spec.get("kind") or "").strip()
        if not kind or kind in ("none", "off", "disabled"):
            continue
        try:
            provider = build_provider(
                kind,
                config=config,
                name=str(spec.get("name") or kind),
                model=spec.get("model"),
            )
        except Exception:
            continue
        ok, _ = provider.available()
        if ok:
            out[str(spec["role"])] = provider
    return out
