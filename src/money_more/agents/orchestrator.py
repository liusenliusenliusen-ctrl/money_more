"""多 Agent 分析：主分析师 + 副分析师 + 综合。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from money_more.llm.providers.base import LLMProvider
from money_more.utils.logging_util import setup_logging

log = setup_logging()

SYNTHESIS_SYSTEM = """你是 money_more 的「综合投研委员」，负责合并两名独立分析师的结论。

## 职责
1. 对照同一份事实数据，阅读 analyst_a / analyst_b 的 JSON 结论
2. 在分歧处给出裁决与理由；一致处保留并强化置信度
3. **输出必须符合原任务 schema**（与 system_task 要求的字段一致）
4. 投资取向：中长线；不要短线交易噪声
5. 在输出中增加字段：
   - "multi_agent": {"agreement": 0~1, "dissent": ["..."], "sources": ["a_name","b_name"]}
6. 只输出合法 JSON
"""


class AnalystAgent:
    def __init__(self, provider: LLMProvider, role: str = "analyst") -> None:
        self.provider = provider
        self.role = role

    @property
    def name(self) -> str:
        return getattr(self.provider, "name", self.role)

    def analyze(
        self,
        system_prompt: str,
        user_payload: dict[str, Any],
        *,
        required_keys: list[str] | None = None,
        temperature: float = 0.3,
    ) -> dict[str, Any]:
        data = self.provider.complete_json(
            system_prompt,
            user_payload,
            temperature=temperature,
            required_keys=required_keys,
        )
        data.setdefault("_agent", self.name)
        data.setdefault("_role", self.role)
        return data


class SynthesisAgent:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    @property
    def name(self) -> str:
        return getattr(self.provider, "name", "synthesizer")

    def synthesize(
        self,
        *,
        task_system_prompt: str,
        user_payload: dict[str, Any],
        analyst_a: dict[str, Any],
        analyst_b: dict[str, Any],
        required_keys: list[str] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "system_task": task_system_prompt[:4000],
            "shared_facts": user_payload,
            "analyst_a": {k: v for k, v in analyst_a.items() if not str(k).startswith("_")},
            "analyst_b": {k: v for k, v in analyst_b.items() if not str(k).startswith("_")},
            "analyst_a_name": analyst_a.get("_agent"),
            "analyst_b_name": analyst_b.get("_agent"),
        }
        return self.provider.complete_json(
            SYNTHESIS_SYSTEM,
            payload,
            temperature=0.2,
            required_keys=required_keys,
        )


class MultiAgentOrchestrator:
    """双分析师并行 + 综合。任一副分析失败时回退到主分析。"""

    def __init__(
        self,
        primary: AnalystAgent,
        secondary: AnalystAgent | None = None,
        synthesizer: SynthesisAgent | None = None,
        *,
        parallel: bool = True,
    ) -> None:
        self.primary = primary
        self.secondary = secondary
        self.synthesizer = synthesizer
        self.parallel = parallel

    def analyze_json(
        self,
        system_prompt: str,
        user_payload: dict[str, Any],
        *,
        required_keys: list[str] | None = None,
        temperature: float = 0.3,
        multi: bool = True,
    ) -> dict[str, Any]:
        if not multi or self.secondary is None or self.synthesizer is None:
            return self.primary.analyze(
                system_prompt,
                user_payload,
                required_keys=required_keys,
                temperature=temperature,
            )

        a_out: dict[str, Any] | None = None
        b_out: dict[str, Any] | None = None
        errors: list[str] = []

        if self.parallel:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futs = {
                    pool.submit(
                        self.primary.analyze,
                        system_prompt,
                        user_payload,
                        required_keys=required_keys,
                        temperature=temperature,
                    ): "primary",
                    pool.submit(
                        self.secondary.analyze,
                        system_prompt,
                        user_payload,
                        required_keys=required_keys,
                        temperature=min(0.5, temperature + 0.1),
                    ): "secondary",
                }
                for fut in as_completed(futs):
                    role = futs[fut]
                    try:
                        out = fut.result()
                        if role == "primary":
                            a_out = out
                        else:
                            b_out = out
                    except Exception as exc:
                        errors.append(f"{role}: {exc}")
                        log.warning("multi-agent %s failed: %s", role, exc)
        else:
            try:
                a_out = self.primary.analyze(
                    system_prompt, user_payload, required_keys=required_keys, temperature=temperature
                )
            except Exception as exc:
                errors.append(f"primary: {exc}")
            try:
                b_out = self.secondary.analyze(
                    system_prompt,
                    user_payload,
                    required_keys=required_keys,
                    temperature=min(0.5, temperature + 0.1),
                )
            except Exception as exc:
                errors.append(f"secondary: {exc}")

        if a_out is None and b_out is None:
            raise RuntimeError("多 Agent 分析全部失败: " + "; ".join(errors))
        if a_out is None:
            b_out = b_out or {}
            b_out["_multi_agent_fallback"] = "secondary_only"
            b_out["_multi_agent_errors"] = errors
            return b_out
        if b_out is None:
            a_out["_multi_agent_fallback"] = "primary_only"
            a_out["_multi_agent_errors"] = errors
            return a_out

        try:
            final = self.synthesizer.synthesize(
                task_system_prompt=system_prompt,
                user_payload=user_payload,
                analyst_a=a_out,
                analyst_b=b_out,
                required_keys=required_keys,
            )
            final["_multi_agent"] = {
                "primary": self.primary.name,
                "secondary": self.secondary.name,
                "synthesizer": self.synthesizer.name,
                "errors": errors,
            }
            final["_analyst_drafts"] = {
                self.primary.name: {k: v for k, v in a_out.items() if not str(k).startswith("_")},
                self.secondary.name: {k: v for k, v in b_out.items() if not str(k).startswith("_")},
            }
            return final
        except Exception as exc:
            log.warning("synthesis failed, use primary: %s", exc)
            a_out["_multi_agent_fallback"] = "synthesis_failed"
            a_out["_multi_agent_errors"] = errors + [f"synthesis: {exc}"]
            a_out["_analyst_drafts"] = {
                self.secondary.name: {k: v for k, v in b_out.items() if not str(k).startswith("_")},
            }
            return a_out


def build_orchestrator(config: Any) -> MultiAgentOrchestrator | None:
    from money_more.llm.providers.factory import build_providers_from_config

    providers = build_providers_from_config(config)
    primary_p = providers.get("primary")
    if primary_p is None:
        return None
    secondary_p = providers.get("secondary")
    synth_p = providers.get("synthesizer") or primary_p
    return MultiAgentOrchestrator(
        primary=AnalystAgent(primary_p, role="primary"),
        secondary=AnalystAgent(secondary_p, role="secondary") if secondary_p else None,
        synthesizer=SynthesisAgent(synth_p) if secondary_p else None,
        parallel=bool(getattr(config.agents, "parallel", True)),
    )
