"""带超时的调用封装（避免 Cursor/LLM 无限挂起）。"""

from __future__ import annotations

import threading
from typing import Any, Callable, TypeVar

T = TypeVar("T")


class LLMTimeoutError(TimeoutError):
    """单次 LLM/Agent 调用超时。"""


def run_with_timeout(fn: Callable[..., T], timeout_seconds: float, *args: Any, **kwargs: Any) -> T:
    """在独立 daemon 线程中执行 fn，超时则抛 LLMTimeoutError。

    为什么不用 ThreadPoolExecutor：池线程会被 concurrent.futures 的
    atexit(_python_exit) join——worker 卡在死 socket 的 recv() 上时，
    主流程即使全部完成，解释器退出也会被永久拖住（服务器上曾累积
    12 个跑完不退的僵尸进程）。daemon 线程不挡解释器退出，
    挂死的调用随进程消亡，调用方超时后正常降级/重试。
    """
    if timeout_seconds <= 0:
        return fn(*args, **kwargs)
    box: dict[str, Any] = {}

    def _target() -> None:
        try:
            box["result"] = fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - 原样透传给调用方
            box["error"] = exc

    t = threading.Thread(target=_target, daemon=True, name="llm-call-timeout")
    t.start()
    t.join(timeout_seconds)
    if t.is_alive():
        raise LLMTimeoutError(f"调用超时（>{timeout_seconds:.0f}s）")
    if "error" in box:
        raise box["error"]
    return box.get("result")
