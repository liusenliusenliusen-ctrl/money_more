"""分析邮件正文：截取结论卡+详细论证，并转成手机可读 HTML / 纯文本。"""

from __future__ import annotations

import html
import re


_DETAILS_RE = re.compile(r"<details\b[^>]*>.*?</details>", re.IGNORECASE | re.DOTALL)
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_CODE_RE = re.compile(r"`([^`]+)`")
_ITALIC_RE = re.compile(r"(?<!\w)_([^_\n]+)_(?!\w)")


def strip_details_blocks(md: str) -> str:
    return _DETAILS_RE.sub("", md or "")


def extract_analysis_email_markdown(full_md: str) -> str:
    """只保留结论卡 + 详细论证（去掉数据源、模拟附录等）。"""
    text = strip_details_blocks(full_md or "")
    lines = text.splitlines()
    start = -1
    for i, line in enumerate(lines):
        if line.startswith("## 结论卡"):
            start = i
            break
    if start < 0:
        # 失败报告等无结论卡时退回全文（仍去掉 details）
        return text.strip()

    chunk = "\n".join(lines[start:]).strip()
    chunk = re.sub(r"\n---\n+\Z", "\n", chunk)
    return chunk.strip()


def _inline_md_to_html(text: str) -> str:
    """先抽 code/bold，再 escape 其余。"""
    parts: list[str] = []
    i = 0
    pattern = re.compile(r"(\*\*.+?\*\*|`[^`]+`)")
    for m in pattern.finditer(text):
        if m.start() > i:
            parts.append(html.escape(text[i : m.start()]))
        token = m.group(0)
        if token.startswith("**") and token.endswith("**"):
            parts.append("<strong>" + html.escape(token[2:-2]) + "</strong>")
        else:
            parts.append("<code>" + html.escape(token[1:-1]) + "</code>")
        i = m.end()
    if i < len(text):
        parts.append(html.escape(text[i:]))
    return "".join(parts)


def _is_table_sep(line: str) -> bool:
    s = line.strip()
    if not s.startswith("|"):
        return False
    cells = [c.strip() for c in s.strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", c or "") for c in cells)


def _parse_table_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def md_to_email_html(md: str) -> str:
    """报告常用 Markdown 子集 → 简易 HTML（邮件客户端友好）。"""
    lines = (md or "").splitlines()
    out: list[str] = []
    i = 0
    in_ul = False
    in_ol = False
    in_bq = False

    def close_lists() -> None:
        nonlocal in_ul, in_ol
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_ol:
            out.append("</ol>")
            in_ol = False

    def close_bq() -> None:
        nonlocal in_bq
        if in_bq:
            out.append("</blockquote>")
            in_bq = False

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # 表格
        if stripped.startswith("|") and i + 1 < len(lines) and _is_table_sep(lines[i + 1]):
            close_lists()
            close_bq()
            headers = _parse_table_row(stripped)
            i += 2
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                if not _is_table_sep(lines[i]):
                    rows.append(_parse_table_row(lines[i].strip()))
                i += 1
            out.append('<div class="table-wrap"><table>')
            out.append("<thead><tr>" + "".join(f"<th>{_inline_md_to_html(h)}</th>" for h in headers) + "</tr></thead>")
            out.append("<tbody>")
            for row in rows:
                # 对齐列数
                while len(row) < len(headers):
                    row.append("")
                out.append(
                    "<tr>"
                    + "".join(f"<td>{_inline_md_to_html(c)}</td>" for c in row[: len(headers)])
                    + "</tr>"
                )
            out.append("</tbody></table></div>")
            continue

        if not stripped:
            close_lists()
            close_bq()
            i += 1
            continue

        if stripped in ("---", "***", "___"):
            close_lists()
            close_bq()
            out.append("<hr>")
            i += 1
            continue

        if stripped.startswith("### "):
            close_lists()
            close_bq()
            out.append(f"<h3>{_inline_md_to_html(stripped[4:])}</h3>")
            i += 1
            continue
        if stripped.startswith("## "):
            close_lists()
            close_bq()
            out.append(f"<h2>{_inline_md_to_html(stripped[3:])}</h2>")
            i += 1
            continue
        if stripped.startswith("# "):
            close_lists()
            close_bq()
            out.append(f"<h1>{_inline_md_to_html(stripped[2:])}</h1>")
            i += 1
            continue

        if stripped.startswith("> "):
            close_lists()
            if not in_bq:
                out.append("<blockquote>")
                in_bq = True
            out.append(f"<p>{_inline_md_to_html(stripped[2:])}</p>")
            i += 1
            continue
        if stripped == ">":
            close_lists()
            if not in_bq:
                out.append("<blockquote>")
                in_bq = True
            i += 1
            continue

        m_ul = re.match(r"^[-*] (.+)$", stripped)
        if m_ul:
            close_bq()
            if in_ol:
                out.append("</ol>")
                in_ol = False
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{_inline_md_to_html(m_ul.group(1))}</li>")
            i += 1
            continue

        m_ol = re.match(r"^(\d+)\. (.+)$", stripped)
        if m_ol:
            close_bq()
            if in_ul:
                out.append("</ul>")
                in_ul = False
            if not in_ol:
                out.append("<ol>")
                in_ol = True
            out.append(f"<li>{_inline_md_to_html(m_ol.group(2))}</li>")
            i += 1
            continue

        close_lists()
        close_bq()
        # 缩进续行（结论卡子点）
        if line.startswith("  - ") or line.startswith("  "):
            content = stripped[2:] if stripped.startswith("- ") else stripped
            out.append(f'<p class="indent">{_inline_md_to_html(content)}</p>')
        else:
            out.append(f"<p>{_inline_md_to_html(stripped)}</p>")
        i += 1

    close_lists()
    close_bq()
    return "\n".join(out)


def wrap_email_html(
    inner: str,
    *,
    run_date: str,
    meta: str | None = None,
) -> str:
    meta_line = html.escape(
        meta
        or (
            f"money_more 分析报告 {run_date} · "
            "正文：结论卡 + 详细论证（完整 md 见附件）"
        )
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>money_more {html.escape(run_date)}</title>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB",
      "Microsoft YaHei", sans-serif;
    font-size: 15px;
    line-height: 1.55;
    color: #222;
    background: #fff;
    margin: 0;
    padding: 14px 12px 28px;
    max-width: 720px;
  }}
  .meta {{ color: #666; font-size: 13px; margin-bottom: 12px; }}
  h1 {{ font-size: 1.3em; margin: 0.6em 0 0.4em; }}
  h2 {{
    font-size: 1.15em;
    margin: 1.35em 0 0.5em;
    padding-bottom: 0.25em;
    border-bottom: 1px solid #e5e5e5;
  }}
  h3 {{ font-size: 1.02em; margin: 1.1em 0 0.4em; }}
  p {{ margin: 0.45em 0; }}
  p.indent {{ margin-left: 0.8em; color: #333; }}
  ul, ol {{ margin: 0.4em 0 0.6em 1.2em; padding: 0; }}
  li {{ margin: 0.25em 0; }}
  blockquote {{
    margin: 0.7em 0;
    padding: 0.45em 0.75em;
    border-left: 3px solid #888;
    background: #f6f6f6;
    color: #333;
  }}
  blockquote p {{ margin: 0.25em 0; }}
  code {{
    font-family: ui-monospace, Menlo, Consolas, monospace;
    font-size: 0.9em;
    background: #f0f0f0;
    padding: 0 4px;
    border-radius: 3px;
  }}
  hr {{ border: none; border-top: 1px solid #ddd; margin: 1.2em 0; }}
  .table-wrap {{ width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch; margin: 0.6em 0; }}
  table {{
    border-collapse: collapse;
    width: 100%;
    min-width: 480px;
    font-size: 12.5px;
  }}
  th, td {{
    border: 1px solid #ccc;
    padding: 6px 8px;
    text-align: left;
    vertical-align: top;
  }}
  th {{ background: #f3f3f3; }}
  .foot {{ color: #888; font-size: 12px; margin-top: 1.5em; }}
</style>
</head>
<body>
<p class="meta">{meta_line}</p>
{inner}
<p class="foot">手机邮件按 HTML 排版；完整 Markdown 见附件。</p>
</body>
</html>
"""


def md_to_plain(md: str) -> str:
    """无 HTML 客户端时的可读纯文本（去 MD 标记）。"""
    text = strip_details_blocks(md or "")
    out: list[str] = []
    for line in text.splitlines():
        s = line.rstrip()
        if s.startswith("### "):
            out.append("")
            out.append(s[4:])
            out.append("-" * min(24, max(8, len(s[4:]))))
            continue
        if s.startswith("## "):
            out.append("")
            out.append(s[3:])
            out.append("=" * min(28, max(10, len(s[3:]))))
            continue
        if s.startswith("# "):
            out.append("")
            out.append(s[2:])
            out.append("=" * min(32, max(12, len(s[2:]))))
            continue
        if s.strip() in ("---", "***", "___"):
            out.append("")
            continue
        if s.startswith("> "):
            out.append("｜ " + s[2:])
            continue
        s = _BOLD_RE.sub(r"\1", s)
        s = _CODE_RE.sub(r"\1", s)
        s = _ITALIC_RE.sub(r"\1", s)
        out.append(s)
    # 压缩过多空行
    plain = "\n".join(out)
    plain = re.sub(r"\n{3,}", "\n\n", plain)
    return plain.strip() + "\n"


def build_analysis_email_bodies(full_md: str, run_date: str) -> tuple[str, str]:
    """返回 (plain, html)。"""
    section = extract_analysis_email_markdown(full_md)
    if not section.strip():
        section = strip_details_blocks(full_md)
    intro = (
        f"money_more 分析报告 {run_date}\n"
        f"（正文为结论卡 + 详细论证；完整 Markdown 见附件。）\n\n"
    )
    plain = intro + md_to_plain(section)
    html_body = wrap_email_html(md_to_email_html(section), run_date=run_date)
    return plain, html_body
