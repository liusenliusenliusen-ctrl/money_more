"""SMTP 邮件通知：分析报告 / 自优化报告。"""

from __future__ import annotations

import mimetypes
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Sequence

from money_more.config import AppConfig, EmailConfig
from money_more.utils.logging_util import setup_logging

log = setup_logging()

# 正文预览上限，完整内容走附件
_BODY_PREVIEW_CHARS = 12000


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
    return text[:limit] + f"\n\n…（正文已截断，完整内容见附件，共 {len(text)} 字符）\n"


def _attach_file(msg: EmailMessage, path: Path) -> None:
    if not path.exists() or not path.is_file():
        return
    ctype, encoding = mimetypes.guess_type(str(path))
    if ctype is None or encoding is not None:
        ctype = "application/octet-stream"
    maintype, subtype = ctype.split("/", 1)
    msg.add_attachment(
        path.read_bytes(),
        maintype=maintype,
        subtype=subtype,
        filename=path.name,
    )


def send_report_email(
    config: AppConfig,
    *,
    subject: str,
    body: str,
    attachments: Sequence[str | Path] | None = None,
) -> dict[str, Any]:
    """发送一封报告邮件；失败不抛到上层业务（返回 error 字段）。"""
    cfg = config.email
    ok, reason = email_ready(cfg)
    if not ok:
        return {"skipped": True, "reason": reason}

    from_addr = (cfg.from_addr or cfg.smtp_user).strip()
    to_list = [a.strip() for a in cfg.to_addrs if a and a.strip()]
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_list)
    msg.set_content(_preview(body), charset="utf-8")

    for item in attachments or []:
        _attach_file(msg, Path(item))

    try:
        if cfg.use_ssl:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, context=context, timeout=60) as smtp:
                smtp.login(cfg.smtp_user, cfg.smtp_password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=60) as smtp:
                smtp.ehlo()
                if cfg.use_tls:
                    context = ssl.create_default_context()
                    smtp.starttls(context=context)
                    smtp.ehlo()
                smtp.login(cfg.smtp_user, cfg.smtp_password)
                smtp.send_message(msg)
        log.info("email sent to %s subject=%s", to_list, subject)
        return {"skipped": False, "ok": True, "to": to_list, "subject": subject}
    except Exception as exc:
        log.warning("email send failed: %s", exc)
        return {"skipped": False, "ok": False, "error": str(exc), "to": to_list, "subject": subject}


def notify_analysis_report(config: AppConfig, report_path: str | Path, run_date: str) -> dict[str, Any]:
    path = Path(report_path)
    body = path.read_text(encoding="utf-8") if path.exists() else f"(找不到报告文件: {path})"
    attachments: list[Path] = [path]
    trend = config.resolve(config.paths.reports) / "trend.md"
    if trend.exists():
        attachments.append(trend)
    return send_report_email(
        config,
        subject=f"[money_more] 分析报告 {run_date}",
        body=f"money_more 分析报告已生成（{run_date}）。\n\n{body}",
        attachments=attachments,
    )


def notify_optimize_report(config: AppConfig, report_path: str | Path, run_date: str) -> dict[str, Any]:
    path = Path(report_path)
    body = path.read_text(encoding="utf-8") if path.exists() else f"(找不到报告文件: {path})"
    return send_report_email(
        config,
        subject=f"[money_more] 自优化报告 {run_date}",
        body=f"money_more 自优化报告已生成（{run_date}）。\n\n{body}",
        attachments=[path],
    )
