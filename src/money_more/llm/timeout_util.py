"""带超时的调用封装（避免 Cursor/LLM 无限挂起）。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Any, Callable, TypeVar

T = TypeVar("T")


class LLMTimeoutError(TimeoutError):
    """单次 LLM/Agent 调用超时。"""


def run_with_timeout(fn: Callable[..., T], timeout_seconds: float, *args: Any, **kwargs: Any) -> T:
    """在独立线程中执行 fn，超时则抛 LLMTimeoutError。

    注意：底层 HTTP/SDK 线程可能仍短暂存活，但调用方可以继续降级/重试。
    """
    if timeout_seconds <= 0:
        return fn(*args, **kwargs)
    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(fn, *args, **kwargs)
        try:
            return fut.result(timeout=timeout_seconds)
        except FuturesTimeout as exc:
            fut.cancel()
            raise LLMTimeoutError(f"调用超时（>{timeout_seconds:.0f}s）") from exc
