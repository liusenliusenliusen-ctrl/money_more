"""run_with_timeout 的 daemon 线程语义：超时快速抛出、线程不挡进程退出。"""

from __future__ import annotations

import threading
import time

import pytest

from money_more.llm.timeout_util import LLMTimeoutError, run_with_timeout


def test_run_with_timeout_returns_result() -> None:
    assert run_with_timeout(lambda x: x + 1, 5.0, 41) == 42


def test_run_with_timeout_raises_and_thread_is_daemon() -> None:
    started = threading.Event()

    def _hang() -> None:
        started.set()
        time.sleep(60)  # 模拟卡在死 socket 上的调用

    t0 = time.time()
    with pytest.raises(LLMTimeoutError):
        run_with_timeout(_hang, 0.3)
    assert time.time() - t0 < 5.0  # 快速抛出，不等 60s
    # 残留线程是 daemon：不挡解释器退出（这是防僵尸进程的关键语义）
    leftovers = [t for t in threading.enumerate() if t.name == "llm-call-timeout"]
    assert leftovers and all(t.daemon for t in leftovers)


def test_run_with_timeout_propagates_errors() -> None:
    def _boom() -> None:
        raise ValueError("inner")

    with pytest.raises(ValueError, match="inner"):
        run_with_timeout(_boom, 5.0)


def test_run_with_timeout_zero_timeout_runs_inline() -> None:
    assert run_with_timeout(lambda: "ok", 0) == "ok"
