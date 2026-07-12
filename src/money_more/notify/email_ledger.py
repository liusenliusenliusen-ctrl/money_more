"""邮件发送台账：记录收件人，并标记是否已发过「如何解读报告」。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from money_more.utils.json_util import dumps_json

_LEDGER_REL = Path("logs") / "email_ledger.json"
_MAX_HISTORY = 200


def ledger_path(project_root: Path) -> Path:
    return Path(project_root) / _LEDGER_REL


def load_ledger(project_root: Path) -> dict[str, Any]:
    path = ledger_path(project_root)
    if not path.exists():
        return {"version": 1, "recipients": {}, "sends": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "recipients": {}, "sends": []}
    if not isinstance(data, dict):
        return {"version": 1, "recipients": {}, "sends": []}
    data.setdefault("version", 1)
    data.setdefault("recipients", {})
    data.setdefault("sends", [])
    if not isinstance(data["recipients"], dict):
        data["recipients"] = {}
    if not isinstance(data["sends"], list):
        data["sends"] = []
    return data


def save_ledger(project_root: Path, data: dict[str, Any]) -> Path:
    path = ledger_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    sends = data.get("sends") or []
    if len(sends) > _MAX_HISTORY:
        data["sends"] = sends[-_MAX_HISTORY:]
    path.write_text(dumps_json(data, indent=2), encoding="utf-8")
    return path


def normalize_addr(addr: str) -> str:
    return (addr or "").strip().lower()


def has_received_guide(project_root: Path, addr: str) -> bool:
    key = normalize_addr(addr)
    if not key:
        return False
    rec = (load_ledger(project_root).get("recipients") or {}).get(key) or {}
    return bool(rec.get("guide_sent_at"))


def split_by_guide_status(project_root: Path, addrs: list[str]) -> tuple[list[str], list[str]]:
    """返回 (首次需附解读文档的地址, 已收过解读文档的地址)，保持原大小写。"""
    first: list[str] = []
    returning: list[str] = []
    for addr in addrs:
        a = (addr or "").strip()
        if not a:
            continue
        if has_received_guide(project_root, a):
            returning.append(a)
        else:
            first.append(a)
    return first, returning


def record_send(
    project_root: Path,
    *,
    to_addrs: list[str],
    subject: str,
    ok: bool,
    kind: str,
    guide_attached: bool = False,
    error: str | None = None,
) -> dict[str, Any]:
    """写入发送历史；成功时更新收件人统计，并在附带解读时打 guide_sent_at。"""
    now = datetime.now().isoformat(timespec="seconds")
    data = load_ledger(project_root)
    recipients: dict[str, Any] = data.setdefault("recipients", {})
    entry = {
        "at": now,
        "to": list(to_addrs),
        "subject": subject,
        "ok": bool(ok),
        "kind": kind,
        "guide_attached": bool(guide_attached),
    }
    if error:
        entry["error"] = error
    data.setdefault("sends", []).append(entry)

    if ok:
        for addr in to_addrs:
            key = normalize_addr(addr)
            if not key:
                continue
            rec = recipients.get(key) or {
                "address": addr.strip(),
                "first_sent_at": now,
                "send_count": 0,
            }
            rec["address"] = addr.strip()
            rec["last_sent_at"] = now
            rec["last_subject"] = subject
            rec["send_count"] = int(rec.get("send_count") or 0) + 1
            if guide_attached and not rec.get("guide_sent_at"):
                rec["guide_sent_at"] = now
            if not rec.get("first_sent_at"):
                rec["first_sent_at"] = now
            recipients[key] = rec

    save_ledger(project_root, data)
    return entry
