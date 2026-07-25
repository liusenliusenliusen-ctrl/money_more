"""分析邮件：结论卡+详细论证 HTML 正文，附件仍为 md。"""

from __future__ import annotations

from money_more.notify.email_format import (
    build_analysis_email_bodies,
    extract_analysis_email_markdown,
    md_to_email_html,
    md_to_plain,
)


SAMPLE = """# money_more 中长线周期决策报告

**日期**: 2026-07-25

## 数据源说明（本轮）

| 状态 | 数据源 |
|------|--------|
| ✅ | 现货 |

## 结论卡（速读）

### 【主结论】分析：现在怎么看

- **环境**: 弱势 · 风险 high
- **配置倾向**: 偏防御

### 【主结论】动作：怎么做（④风控终局）

- **观察** 300750 宁德时代

## 详细论证

_以下为完整分析过程_

## 0. 情报综述（新闻 / 政策 / 舆论）【主结论层】

主题：**美债**上行。

<details>
<summary><strong>附录：模拟账本（评估用 · 非真实持仓）</strong></summary>

- 空仓

</details>

---
*本报告由 AI 生成，仅供参考，不构成投资建议。*
"""


def test_extract_skips_data_sources_and_sim_appendix():
    section = extract_analysis_email_markdown(SAMPLE)
    assert section.startswith("## 结论卡")
    assert "数据源说明" not in section
    assert "模拟账本" not in section
    assert "详细论证" in section
    assert "美债" in section
    assert "仅供参考" in section


def test_html_has_structure_and_no_raw_md_headers():
    plain, html_body = build_analysis_email_bodies(SAMPLE, "2026-07-25")
    assert "结论卡" in plain
    assert "## 数据源" not in plain
    assert "<h2>" in html_body
    assert "结论卡" in html_body
    assert "详细论证" in html_body
    assert "viewport" in html_body
    assert "<strong>美债</strong>" in html_body or "美债" in html_body
    # 不应把原始 ## 标题原样堆进 HTML 可见区作为 md
    assert "## 结论卡" not in html_body


def test_table_and_list_render():
    md = """## 结论卡（速读）

| 代码 | 动作 |
|------|------|
| 300750 | 观察 |

- **买入** 测试
"""
    html_body = md_to_email_html(md)
    assert "<table>" in html_body
    assert "<th>" in html_body
    assert "300750" in html_body
    assert "<ul>" in html_body
    assert "<strong>买入</strong>" in html_body


def test_plain_strips_markers():
    text = md_to_plain("### 【主结论】动作\n\n- **观察** `300750`\n")
    assert "**" not in text
    assert "`" not in text
    assert "观察" in text
    assert "300750" in text
