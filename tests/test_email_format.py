"""分析邮件：正文仅结论卡 HTML；附件为主报告 md。"""

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

### A. 主结论

#### A1. 研究：现在怎么看

- **环境**: 弱势 · 风险 high
- **配置倾向**: 偏防御
- 主题：**美债**上行

#### A3. 建议：怎么做（④风控终局）

- **观察** 300750 宁德时代

## 详细论证

_按结论卡 A→B 展开证据_
_A1 内分主线与争议/未验证假说_

### A. 展开主结论（核对结论卡 A1–A3）
#### A1. 现在怎么看（展开）
##### 主线 · 情报综述
详细论证里的长文不应进邮件正文。
##### 争议与未验证假说（须确认才升权）
详细论证里的长文不应进邮件正文。

## D. 趋势更新（滚动）

- 阶段: 弱势

---
*本报告由 AI 生成，仅供参考，不构成投资建议。*
"""


def test_extract_only_conclusion_card():
    section = extract_analysis_email_markdown(SAMPLE)
    assert section.startswith("## 结论卡")
    assert "数据源说明" not in section
    assert "详细论证" not in section
    assert "趋势更新" not in section
    assert "美债" in section
    assert "300750" in section
    assert "长文不应进邮件正文" not in section


def test_html_has_structure_and_no_raw_md_headers():
    plain, html_body = build_analysis_email_bodies(SAMPLE, "2026-07-25")
    assert "结论卡" in plain
    assert "详细论证" not in plain
    assert "## 数据源" not in plain
    assert "<h2>" in html_body
    assert "结论卡" in html_body
    assert "详细论证" not in html_body
    assert "viewport" in html_body
    assert "<strong>美债</strong>" in html_body or "美债" in html_body
    assert "## 结论卡" not in html_body
    assert "主报告" in plain or "附件" in plain


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
    text = md_to_plain("#### A3. 动作\n\n- **观察** `300750`\n")
    assert "**" not in text
    assert "`" not in text
    assert "观察" in text
    assert "300750" in text
