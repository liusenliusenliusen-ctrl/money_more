"""代码→板块标签：硬编码 + 行业字段归一，供集中度与报告使用。"""

from __future__ import annotations

# 常见龙头硬编码；未命中则用 industry_hint 归一
DEFAULT_CODE_SECTOR = {
    "600519": "白酒",
    "000858": "白酒",
    "000568": "白酒",
    "002304": "白酒",
    "300750": "新能源",
    "002594": "新能源",
    "601012": "新能源",
    "300274": "新能源",
    "002460": "新能源",
    "601318": "保险",
    "601336": "保险",
    "601601": "保险",
    "601166": "银行",
    "600036": "银行",
    "601398": "银行",
    "601288": "银行",
    "600000": "银行",
    "000001": "银行",
    "002415": "半导体",
    "603501": "半导体",
    "688981": "半导体",
    "603986": "半导体",
    "002049": "半导体",
    "600276": "医药",
    "000661": "医药",
    "300760": "医药",
    "600887": "食品饮料",
    "000333": "家电",
    "000651": "家电",
    "601888": "旅游",
    "600030": "券商",
    "300059": "券商",
    "002230": "软件",
    "688111": "软件",
}

# 东财/同花顺行业长名 → 短标签
_INDUSTRY_ALIASES = (
    (("白酒", "酿酒", "酒类"), "白酒"),
    (("银行",), "银行"),
    (("保险",), "保险"),
    (("证券", "券商"), "券商"),
    (("半导体", "芯片", "集成电路"), "半导体"),
    (("电子化学品", "电子化学", "湿电子", "光刻胶", "电子气体"), "电子化学品"),
    (("元件", "PCB", "被动元件", "连接器", "电容", "电阻"), "元件"),
    (("光伏", "电池", "锂电", "新能源", "风电", "储能"), "新能源"),
    (("医药", "生物制品", "化学制药", "医疗器械", "中药"), "医药"),
    (("白酒", "饮料", "食品"), "食品饮料"),
    (("家电", "白色家电", "黑色家电"), "家电"),
    (("软件", "计算机", "互联网", "云计算", "人工智能"), "软件"),
    (("汽车", "整车", "零部件"), "汽车"),
    (("房地产", "开发"), "地产"),
    (("煤炭",), "煤炭"),
    (("有色", "铜", "铝", "黄金"), "有色"),
    (("石油", "油气", "石化"), "石油石化"),
    (("军工", "航空", "航天", "船舶"), "军工"),
    (("电力", "公用事业"), "电力"),
    (("通信", "5G", "运营商"), "通信"),
    (("传媒", "游戏", "广告"), "传媒"),
    (("旅游", "酒店", "餐饮"), "旅游"),
)


def normalize_industry(hint: str | None) -> str | None:
    """把行业/概念长名压成短标签。"""
    if not hint:
        return None
    text = str(hint).strip()
    if not text or text.lower() in ("unknown", "none", "nan"):
        return None
    for keys, label in _INDUSTRY_ALIASES:
        if any(k in text for k in keys):
            return label
    # 取前 4 字作粗标签，避免完全 unknown
    return text[:6]


def infer_sector(
    code: str,
    watch_sectors: list[str] | None = None,
    industry_hint: str | None = None,
) -> str | None:
    """推断板块标签。优先硬编码，其次行业字段；不再要求必须落在 watch_sectors。"""
    code = "".join(ch for ch in str(code) if ch.isdigit())[-6:].zfill(6)
    mapped = DEFAULT_CODE_SECTOR.get(code)
    if mapped:
        return mapped
    return normalize_industry(industry_hint)


def industry_hint_from_sources(
    quote: dict | None = None,
    company: dict | None = None,
    analysis: dict | None = None,
) -> str | None:
    """从行情/公司/LLM 分析里抽行业提示。"""
    quote = quote or {}
    company = company or {}
    analysis = analysis or {}
    for key in ("所属行业", "行业", "industry"):
        v = quote.get(key) or company.get(key)
        if v:
            return str(v)
    for key in ("industry", "main_business", "business"):
        v = company.get(key)
        if v:
            return str(v)
    # tushare stock_company 常见字段
    for key in ("industry", "主业", "introduce"):
        v = company.get(key)
        if v:
            return str(v)
    sec = analysis.get("sector") or analysis.get("sector_tag")
    if sec:
        return str(sec)
    return None
