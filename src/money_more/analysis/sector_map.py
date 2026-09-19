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
    "002371": "半导体",
    "688012": "半导体",
    "688256": "半导体",
    "300604": "半导体",
    "002156": "半导体",
    "002185": "半导体",
    "688825": "半导体",
    "300308": "通信",
    "300502": "通信",
    "300394": "通信",
    "600487": "通信",
    "000938": "软件",
    "000725": "元件",
    "001309": "半导体",
    "000021": "元件",
    "002384": "元件",
    "600183": "元件",
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
    "600176": "建材",
}

# 以申万一级为底（约 31 类），另保留产品细项：白酒 / 半导体 / 元件 / 新能源 / 银行·保险·券商。
# 别名用较长词；匹配时取最长命中，避免「航空」把航空运输打成军工。
_INDUSTRY_ALIASES = (
    (("白酒", "酿酒", "酒类"), "白酒"),
    (("银行",), "银行"),
    (("保险",), "保险"),
    (("证券", "券商", "非银金融", "多元金融"), "券商"),
    (
        ("半导体", "芯片", "集成电路", "封测", "光刻", "晶圆", "存储芯片", "DRAM", "NAND"),
        "半导体",
    ),
    (
        ("光模块", "光通信", "光纤", "光器件", "CPO", "通信设备", "运营商", "5G"),
        "通信",
    ),
    (("通信",), "通信"),
    (("电子化学品", "电子化学", "湿电子", "光刻胶", "电子气体"), "电子化学品"),
    (
        ("显示器件", "面板", "液晶", "OLED", "PCB", "覆铜板", "被动元件", "连接器", "光电子"),
        "元件",
    ),
    (("元件", "电容", "电阻"), "元件"),
    (("消费电子", "电子制造"), "电子"),
    (("电子",), "电子"),
    (
        ("光伏", "锂电", "锂电池", "动力电池", "新能源", "风电", "储能", "电力设备"),
        "新能源",
    ),
    (("电池",), "新能源"),
    (("安防设备", "智能安防", "安防"), "元件"),
    (("化学制药", "生物制品", "医疗器械", "中药", "医药生物", "创新药", "医药"), "医药"),
    (("食品饮料", "饮料", "食品"), "食品饮料"),
    (("白色家电", "黑色家电", "家用电器", "家电"), "家电"),
    (("计算机", "互联网", "云计算", "软件"), "软件"),
    (("人工智能",), "软件"),
    (("汽车整车", "汽车零部件", "汽车"), "汽车"),
    (("房地产开发", "房地产", "地产"), "地产"),
    (("煤炭",), "煤炭"),
    (("有色金属", "工业金属", "黄金开采", "电解铝", "铜业", "有色"), "有色"),
    (("石油石化", "油气", "石化", "石油"), "石油石化"),
    (("国防军工", "航空装备", "航天装备", "地面兵装", "军工", "航天", "兵器"), "军工"),
    (("船舶制造",), "军工"),
    (("公用事业", "火力发电", "水力发电", "电网", "输变电", "电力"), "电力"),
    (("传媒", "游戏", "广告", "影视"), "传媒"),
    (("酒店餐饮", "旅游", "酒店", "餐饮", "景区"), "旅游"),
    (("建筑材料", "玻璃纤维", "玻璃制造", "水泥", "玻纤", "建材"), "建材"),
    (("玻璃",), "建材"),
    (("新材料",), "建材"),
    (("基础化工", "化学原料", "化学制品", "农药", "氯碱", "化肥", "化工"), "化工"),
    (("钢铁",), "钢铁"),
    (("农林牧渔", "种植业", "畜牧", "饲料", "渔业", "农产品", "农业"), "农林牧渔"),
    (
        ("航空运输", "航空机场", "航运", "海运", "港口", "物流", "铁路", "公路", "快递", "交通运输"),
        "交通运输",
    ),
    (("建筑装饰", "基建", "工程建造", "建筑"), "建筑"),
    (("机械设备", "工程机械", "仪器仪表", "自动化设备", "机床", "机械"), "机械"),
    (("环保", "固废", "污水处理", "大气治理"), "环保"),
    (("纺织服饰", "纺织", "服装", "服饰", "印染"), "纺织服饰"),
    (("轻工制造", "造纸", "包装印刷", "家具", "轻工"), "轻工"),
    (("商贸零售", "电商零售", "超市", "百货", "零售"), "零售"),
    (("美容护理", "化妆品", "医美", "护肤"), "美容护理"),
    (("社会服务", "专业服务", "体育"), "社会服务"),
)


# 粗主题：深度池分散用（半导体/光模块/元件等同属科技硬件）
_THEME_MEMBERS: dict[str, frozenset[str]] = {
    "科技硬件": frozenset({"半导体", "元件", "电子化学品", "电子", "软件", "通信", "传媒"}),
    "新能源": frozenset({"新能源"}),
    "金融": frozenset({"银行", "保险", "券商"}),
    "消费": frozenset(
        {"白酒", "食品饮料", "家电", "旅游", "零售", "纺织服饰", "美容护理", "社会服务"}
    ),
    "医药": frozenset({"医药"}),
    "周期": frozenset(
        {
            "有色",
            "煤炭",
            "石油石化",
            "军工",
            "电力",
            "汽车",
            "地产",
            "建材",
            "化工",
            "钢铁",
            "农林牧渔",
            "交通运输",
            "建筑",
            "机械",
            "环保",
            "轻工",
        }
    ),
}

_KNOWN_SECTOR_LABELS = frozenset(
    label for members in _THEME_MEMBERS.values() for label in members
) | frozenset(label for _keys, label in _INDUSTRY_ALIASES)


def _strip_sector_affix(text: str) -> str:
    return text.replace("板块", "").replace("行业", "").strip()


def normalize_industry(hint: str | None) -> str | None:
    """把行业/概念长名压成短标签。未命中别名时返回 None，避免把公司名截成「板块」。"""
    if not hint:
        return None
    text = str(hint).strip()
    if not text or text.lower() in ("unknown", "none", "nan"):
        return None
    stripped = _strip_sector_affix(text)
    if stripped in _KNOWN_SECTOR_LABELS:
        return stripped
    best_len = 0
    best_label: str | None = None
    for keys, label in _INDUSTRY_ALIASES:
        for key in keys:
            if key and key in text and len(key) > best_len:
                best_len = len(key)
                best_label = label
    return best_label


def is_known_sector_label(name: str | None) -> bool:
    """行业短名 / 别名命中才算板块；个股名（东山精密、长鑫科技）不得进板块对照表。"""
    text = str(name or "").strip()
    if not text:
        return False
    stripped = _strip_sector_affix(text)
    if text in _KNOWN_SECTOR_LABELS or stripped in _KNOWN_SECTOR_LABELS:
        return True
    if any(tok in text for tok in ("股份", "集团", "控股", "有限", "精密", "康德")):
        return False
    if normalize_industry(text):
        return True
    if text.endswith(("科技", "生物", "电子")):
        return False
    return False


def sanitize_sector_label(name: str | None, *, code: str | None = None) -> str | None:
    """只保留已知行业短名；公司名丢弃。可按代码回填硬编码映射。"""
    if is_known_sector_label(name):
        return normalize_industry(name) or _strip_sector_affix(str(name or ""))
    if code:
        mapped = infer_sector(code)
        if mapped and is_known_sector_label(mapped):
            return mapped
    return None


def infer_sector(
    code: str,
    watch_sectors: list[str] | None = None,
    industry_hint: str | None = None,
) -> str | None:
    """推断板块标签。优先硬编码，其次行业字段；不再要求必须落在 watch_sectors。"""
    _ = watch_sectors
    code = "".join(ch for ch in str(code) if ch.isdigit())[-6:].zfill(6)
    mapped = DEFAULT_CODE_SECTOR.get(code)
    if mapped:
        return mapped
    return normalize_industry(industry_hint)


def theme_bucket(sector: str | None) -> str:
    """细板块 → 粗主题，用于深度池赛道上限。"""
    if not sector:
        return "其他"
    label = str(sector).strip()
    if not label:
        return "其他"
    for theme, members in _THEME_MEMBERS.items():
        if label in members:
            return theme
    # 未归一长名再试一次
    norm = normalize_industry(label)
    if norm and norm != label:
        for theme, members in _THEME_MEMBERS.items():
            if norm in members:
                return theme
    return "其他"


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
