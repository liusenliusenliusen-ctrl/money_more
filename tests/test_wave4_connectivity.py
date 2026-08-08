"""第四波：错误分级、bypass、RSSHub feeds、降级话术、观察扩压缩。"""

from __future__ import annotations

import os

from money_more.analysis.degrade_messages import (
    build_screen_degrade_note,
    suggest_from_err_class,
)
from money_more.analysis.pipeline import DecisionPipeline
from money_more.data.ak_direct import (
    classify_em_error,
    eastmoney_direct_session,
    set_eastmoney_bypass_mode,
    set_eastmoney_force_direct,
)
from money_more.data.rss_feeds import feeds_from_rsshub_base


def test_classify_em_error() -> None:
    assert classify_em_error("ProxyError tunnel failed") == "proxy"
    assert classify_em_error("Read timed out") == "timeout"
    assert classify_em_error("spot_empty") == "empty"
    assert classify_em_error("HTTP 429 Too Many Requests") == "http"


def test_bypass_nested_restores_proxy() -> None:
    set_eastmoney_force_direct(True)
    set_eastmoney_bypass_mode("env_clear")
    os.environ["HTTP_PROXY"] = "http://127.0.0.1:9"
    with eastmoney_direct_session():
        assert "HTTP_PROXY" not in os.environ
        with eastmoney_direct_session():
            assert "HTTP_PROXY" not in os.environ
        assert "HTTP_PROXY" not in os.environ
    assert os.environ.get("HTTP_PROXY") == "http://127.0.0.1:9"
    os.environ.pop("HTTP_PROXY", None)
    set_eastmoney_bypass_mode("both")


def test_rsshub_base_feeds() -> None:
    feeds = feeds_from_rsshub_base("https://rss.example.com")
    assert feeds[0]["url"].startswith("https://rss.example.com/cls/")


def test_screen_degrade_note_has_err_class() -> None:
    note = build_screen_degrade_note(
        {
            "spot_source": "sina",
            "errors": ["spot(em_all)[proxy]: ProxyError"],
            "degraded": True,
        }
    )
    assert "错误类=proxy" in note
    assert "代理" in suggest_from_err_class("proxy")


def test_compact_observe_payload() -> None:
    payload = {
        "sector_data": {"constituents": list(range(20)), "name": "煤炭"},
        "sector_intelligence": {"news": list(range(10))},
        "intelligence_digest": {"summary": "x", "noise": "y"},
        "past_lessons": list(range(8)),
        "prior_sector_series": list(range(6)),
    }
    out = DecisionPipeline._compact_sector_llm_payload(payload)
    assert len(out["sector_data"]["constituents"]) == 8
    assert len(out["sector_intelligence"]["news"]) == 5
    assert "noise" not in out["intelligence_digest"]
    assert out["compact_mode"] == "auto_observe"
