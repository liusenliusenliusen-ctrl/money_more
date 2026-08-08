"""东财/AkShare 调用时绕过系统代理（push2 常被代理掐断）。

第四波深治：
- bypass 模式：env_clear | session_trust_env_false | both
- 错误分级：proxy / timeout / empty / http / other
- 嵌套 session 用深度计数，避免内层 finally 提前还原
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Iterator

# 第五波 C5：环境变量与 Session monkey-patch 是进程级全局状态；
# 筛股均额等路径有并发，必须加锁避免「谁都不清 env」窗口。
_BYPASS_LOCK = threading.RLock()

_PROXY_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)

_FORCE_DIRECT: bool = True
_BYPASS_MODE: str = "both"  # env_clear | session_trust_env_false | both | off
_DIRECT_DEPTH: int = 0
_SAVED_ENV: dict[str, str | None] = {}
_SAVED_NO_PROXY: str | None = None
_SAVED_NO_PROXY_L: str | None = None
_SESSION_PATCHED = False
_ORIG_SESSION_REQUEST = None


def set_eastmoney_force_direct(enabled: bool) -> None:
    global _FORCE_DIRECT
    _FORCE_DIRECT = bool(enabled)


def eastmoney_force_direct_enabled() -> bool:
    return bool(_FORCE_DIRECT)


def set_eastmoney_bypass_mode(mode: str) -> None:
    global _BYPASS_MODE
    m = (mode or "both").strip().lower()
    if m not in ("env_clear", "session_trust_env_false", "both", "off"):
        m = "both"
    _BYPASS_MODE = m


def eastmoney_bypass_mode() -> str:
    return _BYPASS_MODE


def classify_em_error(exc: BaseException | str) -> str:
    """将异常/错误串归为 proxy|timeout|empty|http|other。"""
    text = str(exc or "").lower()
    if not text:
        return "other"
    if any(k in text for k in ("proxy", "tunnel", "407", "proxyerror")):
        return "proxy"
    if any(k in text for k in ("timeout", "timed out", "time out", "deadline")):
        return "timeout"
    if any(k in text for k in ("empty", "spot_empty", "hist_empty", "无数据")):
        return "empty"
    if any(
        k in text
        for k in (
            "429",
            "403",
            "502",
            "503",
            "http",
            "status code",
            "too many requests",
            "限流",
        )
    ):
        return "http"
    return "other"


def annotate_em_error(prefix: str, exc: BaseException | str) -> str:
    return f"{prefix}[{classify_em_error(exc)}]: {exc}"


_DEFAULT_HTTP_TIMEOUT: float = 20.0


def set_default_http_timeout(seconds: float) -> None:
    """第五波 C6：bypass 会话内给 requests 调用补默认超时，防 akshare 挂死。"""
    global _DEFAULT_HTTP_TIMEOUT
    try:
        _DEFAULT_HTTP_TIMEOUT = max(3.0, float(seconds))
    except (TypeError, ValueError):
        pass


def _patch_requests_trust_env(active: bool) -> None:
    global _SESSION_PATCHED, _ORIG_SESSION_REQUEST
    try:
        import requests
    except Exception:
        return
    if active and not _SESSION_PATCHED:
        _ORIG_SESSION_REQUEST = requests.Session.request

        def _wrapped(self, method, url, **kwargs):  # type: ignore[no-untyped-def]
            try:
                self.trust_env = False
            except Exception:
                pass
            # C6：调用方未显式给 timeout 时补默认，避免东财挂起拖死线程池
            if kwargs.get("timeout") is None:
                kwargs["timeout"] = _DEFAULT_HTTP_TIMEOUT
            return _ORIG_SESSION_REQUEST(self, method, url, **kwargs)

        requests.Session.request = _wrapped  # type: ignore[method-assign]
        _SESSION_PATCHED = True
    elif not active and _SESSION_PATCHED and _ORIG_SESSION_REQUEST is not None:
        requests.Session.request = _ORIG_SESSION_REQUEST  # type: ignore[method-assign]
        _SESSION_PATCHED = False
        _ORIG_SESSION_REQUEST = None


def _enter_bypass(do_env: bool, do_session: bool) -> None:
    global _DIRECT_DEPTH, _SAVED_ENV, _SAVED_NO_PROXY, _SAVED_NO_PROXY_L
    with _BYPASS_LOCK:
        _DIRECT_DEPTH += 1
        if _DIRECT_DEPTH != 1:
            return
        if do_env:
            _SAVED_ENV = {k: os.environ.pop(k, None) for k in _PROXY_KEYS}
            _SAVED_NO_PROXY = os.environ.get("NO_PROXY")
            _SAVED_NO_PROXY_L = os.environ.get("no_proxy")
            os.environ["NO_PROXY"] = "*"
            os.environ["no_proxy"] = "*"
        if do_session:
            _patch_requests_trust_env(True)


def _leave_bypass(do_env: bool, do_session: bool) -> None:
    global _DIRECT_DEPTH, _SAVED_ENV, _SAVED_NO_PROXY, _SAVED_NO_PROXY_L
    with _BYPASS_LOCK:
        if _DIRECT_DEPTH <= 0:
            return
        _DIRECT_DEPTH -= 1
        if _DIRECT_DEPTH != 0:
            return
        if do_session:
            _patch_requests_trust_env(False)
        if do_env:
            for k, v in _SAVED_ENV.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            if _SAVED_NO_PROXY is None:
                os.environ.pop("NO_PROXY", None)
            else:
                os.environ["NO_PROXY"] = _SAVED_NO_PROXY
            if _SAVED_NO_PROXY_L is None:
                os.environ.pop("no_proxy", None)
            else:
                os.environ["no_proxy"] = _SAVED_NO_PROXY_L
            _SAVED_ENV = {}
            _SAVED_NO_PROXY = None
            _SAVED_NO_PROXY_L = None


@contextmanager
def eastmoney_direct_session(enabled: bool | None = None) -> Iterator[None]:
    """按 bypass 模式临时绕过代理（支持嵌套）。"""
    use = _FORCE_DIRECT if enabled is None else bool(enabled)
    mode = _BYPASS_MODE
    if not use or mode == "off":
        yield
        return

    do_env = mode in ("env_clear", "both")
    do_session = mode in ("session_trust_env_false", "both")
    _enter_bypass(do_env, do_session)
    try:
        yield
    finally:
        _leave_bypass(do_env, do_session)
