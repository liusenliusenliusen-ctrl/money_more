"""SMTP 邮件通知：分析报告。"""

from __future__ import annotations

import mimetypes
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Sequence

from money_more.config import AppConfig, EmailConfig
from money_more.notify.email_format import build_analysis_email_bodies
from money_more.notify.email_ledger import record_send, split_by_guide_status
from money_more.utils.logging_util import setup_logging

log = setup_logging()

# 纯文本后备上限；HTML 正文不截断（完整 md 仍在附件）
_BODY_PREVIEW_CHARS = 20000
_GUIDE_REL = Path("docs") / "how-to-read-report.md"


def email_ready(cfg: EmailConfig) -> tuple[bool, str]:
    if not cfg.enabled:
        return False, "email.enabled=false"
    if not cfg.to_addrs:
        return False, "未配置收件人 EMAIL_TO / email.to"
    if not cfg.smtp_host:
        return False, "未配置 SMTP_HOST"
    if not cfg.smtp_user or not cfg.smtp_password:
        return False, "未配置 SMTP_USER / SMTP_PASSWORD（邮箱授权码）"
    if not (cfg.from_addr or cfg.smtp_user):
        return False, "未配置 EMAIL_FROM"
    return True, "ok"


def _preview(text: str, limit: int = _BODY_PREVIEW_CHARS) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n…（纯文本已截断；请看 HTML 正文或附件完整 md，共 {len(text)} 字符）\n"


def _attach_file(msg: EmailMessage, path: Path) -> None:
    if not path.exists() or not path.is_file():
        return
    ctype, encoding = mimetypes.guess_type(str(path))
    if ctype is None or encoding is not None:
        # .md 常被猜成 octet-stream；标明为 text/markdown 便于手机识别
        if path.suffix.lower() == ".md":
            ctype = "text/markdown"
        else:
            ctype = "application/octet-stream"
    maintype, subtype = ctype.split("/", 1)
    msg.add_attachment(
        path.read_bytes(),
        maintype=maintype,
        subtype=subtype,
        filename=path.name,
    )


def guide_doc_path(config: AppConfig) -> Path:
    return Path(config.project_root) / _GUIDE_REL


def send_report_email(
    config: AppConfig,
    *,
    subject: str,
    body: str,
    html_body: str | None = None,
    attachments: Sequence[str | Path] | None = None,
    to_addrs: Sequence[str] | None = None,
    kind: str = "generic",
    guide_attached: bool = False,
) -> dict[str, Any]:
    """发送一封报告邮件；失败不抛到上层业务（返回 error 字段）。

    body: 纯文本后备；html_body: 手机可读 HTML（优先展示）。
    attachments: 完整 Markdown 等文件。
    """
    cfg = config.email
    ok, reason = email_ready(cfg)
    if not ok:
        return {"skipped": True, "reason": reason}

    from_addr = (cfg.from_addr or cfg.smtp_user).strip()
    to_list = [a.strip() for a in (to_addrs if to_addrs is not None else cfg.to_addrs) if a and str(a).strip()]
    if not to_list:
        return {"skipped": True, "reason": "收件人为空"}

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_list)
    msg.set_content(_preview(body), charset="utf-8")
    if html_body:
        msg.add_alternative(html_body, subtype="html", charset="utf-8")

    for item in attachments or []:
        _attach_file(msg, Path(item))

    try:
        if cfg.use_ssl:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, context=context, timeout=60) as smtp:
                smtp.login(cfg.smtp_user, cfg.smtp_password)
                smtp.send_message(msg, to_addrs=to_list)
        else:
            with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=60) as smtp:
                smtp.ehlo()
                if cfg.use_tls:
                    context = ssl.create_default_context()
                    smtp.starttls(context=context)
                    smtp.ehlo()
                smtp.login(cfg.smtp_user, cfg.smtp_password)
                smtp.send_message(msg, to_addrs=to_list)
        log.info(
            "email sent to %s subject=%s guide=%s html=%s",
            to_list,
            subject,
            guide_attached,
            bool(html_body),
        )
        record_send(
            config.project_root,
            to_addrs=to_list,
            subject=subject,
            ok=True,
            kind=kind,
            guide_attached=guide_attached,
        )
        return {
            "skipped": False,
            "ok": True,
            "to": to_list,
            "subject": subject,
            "guide_attached": guide_attached,
        }
    except Exception as exc:
        log.warning("email send failed: %s", exc)
        record_send(
            config.project_root,
            to_addrs=to_list,
            subject=subject,
            ok=False,
            kind=kind,
            guide_attached=guide_attached,
            error=str(exc),
        )
        return {
            "skipped": False,
            "ok": False,
            "error": str(exc),
            "to": to_list,
            "subject": subject,
            "guide_attached": guide_attached,
        }


def _send_with_optional_guide(
    config: AppConfig,
    *,
    subject: str,
    body: str,
    html_body: str | None,
    attachments: list[Path],
    kind: str,
) -> dict[str, Any]:
    """按收件人是否首次收到，拆成两批发；首次附带 how-to-read-report.md。"""
    cfg = config.email
    ok, reason = email_ready(cfg)
    if not ok:
        return {"skipped": True, "reason": reason}

    to_list = [a.strip() for a in cfg.to_addrs if a and a.strip()]
    first, returning = split_by_guide_status(config.project_root, to_list)
    guide = guide_doc_path(config)
    guide_ok = guide.exists()

    batches: list[dict[str, Any]] = []
    if first:
        atts = list(attachments)
        body_first = body
        html_first = html_body
        attached = False
        if guide_ok:
            atts.append(guide)
            attached = True
            note = (
                "【首次说明】附件含《如何解读报告》(how-to-read-report.md)，"
                "建议先读结论卡再看详细论证；之后邮件不再重复附送。"
            )
            body_first = body + "\n\n---\n" + note + "\n"
            if html_first:
                html_first = html_first.replace(
                    "</body>",
                    f'<p class="foot">{note}</p></body>',
                    1,
                )
        else:
            log.warning("guide doc missing: %s", guide)
        batches.append(
            send_report_email(
                config,
                subject=subject,
                body=body_first,
                html_body=html_first,
                attachments=atts,
                to_addrs=first,
                kind=kind,
                guide_attached=attached,
            )
        )
    if returning:
        batches.append(
            send_report_email(
                config,
                subject=subject,
                body=body,
                html_body=html_body,
                attachments=attachments,
                to_addrs=returning,
                kind=kind,
                guide_attached=False,
            )
        )

    if not batches:
        return {"skipped": True, "reason": "收件人为空"}

    guide_sent_to: list[str] = []
    for b in batches:
        if b.get("ok") and b.get("guide_attached"):
            guide_sent_to.extend(list(b.get("to") or []))

    ok_all = all(bool(b.get("ok")) for b in batches)
    any_fail = [b for b in batches if not b.get("ok")]
    return {
        "skipped": False,
        "ok": ok_all,
        "batches": batches,
        "to": to_list,
        "guide_sent_to": guide_sent_to,
        "subject": subject,
        "error": "; ".join(str(b.get("error")) for b in any_fail if b.get("error")) or None,
    }


def notify_analysis_report(config: AppConfig, report_path: str | Path, run_date: str) -> dict[str, Any]:
    """分析报告邮件：正文=结论卡（HTML）；附件=仅主报告 md（不含复盘/模拟/趋势）。"""
    path = Path(report_path)
    full_md = path.read_text(encoding="utf-8") if path.exists() else f"(找不到报告文件: {path})"
    plain, html_body = build_analysis_email_bodies(full_md, run_date)
    return _send_with_optional_guide(
        config,
        subject=f"[money_more] 分析报告 {run_date}",
        body=plain,
        html_body=html_body,
        attachments=[path],
        kind="analysis",
    )

