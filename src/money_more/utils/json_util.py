from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

import pandas as pd


def json_safe(value: Any) -> Any:
    """递归转换对象为 JSON 可序列化格式。"""
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, float) and pd.isna(value):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    if hasattr(value, "item"):
        try:
            return json_safe(value.item())
        except Exception:
            pass
    return str(value)


def dumps_json(data: Any, **kwargs: Any) -> str:
    return json.dumps(json_safe(data), ensure_ascii=False, **kwargs)
