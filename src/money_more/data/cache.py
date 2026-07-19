"""简单磁盘 TTL 缓存，降低同日重复拉全市场表的成本。"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable

from money_more.utils.json_util import dumps_json


class DiskTTLCache:
    def __init__(self, root: Path, default_ttl_sec: int = 3600) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.default_ttl_sec = default_ttl_sec

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:40]
        return self.root / f"{digest}.json"

    def get(self, key: str) -> Any | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if time.time() > float(payload.get("expires_at", 0)):
                path.unlink(missing_ok=True)
                return None
            return payload.get("value")
        except Exception:
            return None

    def get_stale(self, key: str) -> Any | None:
        """读取已过 TTL 的缓存（不删除文件），供行情全源失败时降级。"""
        path = self._path(key)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload.get("value")
        except Exception:
            return None

    def set(self, key: str, value: Any, ttl_sec: int | None = None) -> None:
        ttl = self.default_ttl_sec if ttl_sec is None else ttl_sec
        path = self._path(key)
        path.write_text(
            dumps_json({"expires_at": time.time() + ttl, "value": value}),
            encoding="utf-8",
        )

    def get_or_set(self, key: str, factory: Callable[[], Any], ttl_sec: int | None = None) -> Any:
        hit = self.get(key)
        if hit is not None:
            return hit
        value = factory()
        try:
            self.set(key, value, ttl_sec=ttl_sec)
        except Exception:
            pass
        return value
