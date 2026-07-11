"""从配置 watch 映射代码→板块，供集中度与报告使用。"""

from __future__ import annotations

# 可按需扩展；未命中则返回 None
DEFAULT_CODE_SECTOR = {
    "600519": "白酒",
    "000858": "白酒",
    "000568": "白酒",
    "300750": "新能源",
    "002594": "新能源",
    "601012": "新能源",
    "601318": "银行",  # 综合金融，近似归银行/保险防御
    "601166": "银行",
    "600036": "银行",
    "002415": "半导体",
    "603501": "半导体",
    "688981": "半导体",
}


def infer_sector(code: str, watch_sectors: list[str] | None = None) -> str | None:
    code = "".join(ch for ch in str(code) if ch.isdigit())[-6:].zfill(6)
    mapped = DEFAULT_CODE_SECTOR.get(code)
    if mapped and (not watch_sectors or mapped in watch_sectors):
        return mapped
    return mapped
