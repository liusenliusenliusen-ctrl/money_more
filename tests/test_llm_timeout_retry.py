"""超时与多 Agent 降级相关单测。"""

from __future__ import annotations

import time

import pytest

from money_more.agents.orchestrator import AnalystAgent, MultiAgentOrchestrator, SynthesisAgent
from money_more.llm.timeout_util import LLMTimeoutError, run_with_timeout


def test_run_with_timeout_ok() -> None:
    assert run_with_timeout(lambda: 42, 1.0) == 42


def test_run_with_timeout_raises() -> None:
    def slow() -> int:
        time.sleep(2.0)
        return 1

    with pytest.raises(LLMTimeoutError):
        run_with_timeout(slow, 0.2)


class _FailProvider:
    name = "fail"

    def complete_json(self, *args, **kwargs):
        raise RuntimeError("boom")


class _OkProvider:
    name = "ok"

    def complete_json(self, *args, **kwargs):
        return {"recommendations": [{"code": "600519", "action": "hold"}], "portfolio_summary": "ok"}


def test_orchestrator_degrades_to_primary_only() -> None:
    orch = MultiAgentOrchestrator(
        primary=AnalystAgent(_OkProvider(), role="primary"),
        secondary=AnalystAgent(_FailProvider(), role="secondary"),
        synthesizer=SynthesisAgent(_OkProvider()),
        parallel=False,
        agent_wait_seconds=5,
    )
    out = orch.analyze_json("sys", {}, required_keys=["recommendations", "portfolio_summary"])
    assert out.get("_multi_agent_fallback") == "primary_only"
    assert out.get("recommendations")


def test_orchestrator_all_failed_returns_not_raise() -> None:
    orch = MultiAgentOrchestrator(
        primary=AnalystAgent(_FailProvider(), role="primary"),
        secondary=AnalystAgent(_FailProvider(), role="secondary"),
        synthesizer=SynthesisAgent(_FailProvider()),
        parallel=False,
        agent_wait_seconds=5,
    )
    out = orch.analyze_json("sys", {})
    assert out.get("_multi_agent_fallback") == "all_failed"
    assert "失败" in str(out.get("portfolio_summary") or "")
