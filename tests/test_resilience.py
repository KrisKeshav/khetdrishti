"""Fault-injection tests: break each layer on purpose and check the system degrades honestly — it labels
stale data, switches provider, times out cleanly, and never invents a value. All fixtures stay in tests/."""
import time
from datetime import timedelta
from types import SimpleNamespace as NS

import httpx
import pytest

from app import fetch
from app.agent import llm, tools
from app.data_sources import mandi, weather
from app.engine import report
from app.fetch import DataUnavailable, fetch_json


# ------------------------------------------------------------------ fetch layer

def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def mock_http(monkeypatch):
    def install(handler):
        monkeypatch.setattr(fetch, "_client", _client(handler))
    yield install
    fetch.clear_outages()


def test_live_then_stale_when_provider_dies(mock_http):
    mock_http(lambda req: httpx.Response(200, json={"v": 1}))
    data, prov = fetch_json("T1", "https://example.test/a", ttl=timedelta(seconds=0))
    assert data == {"v": 1} and prov.mode == "live"
    mock_http(lambda req: httpx.Response(503))
    data, prov = fetch_json("T1", "https://example.test/a", ttl=timedelta(seconds=0), retries=0)
    assert data == {"v": 1} and prov.mode == "stale" and "503" in prov.note


def test_no_cache_and_dead_provider_raises_instead_of_inventing(mock_http):
    mock_http(lambda req: httpx.Response(500))
    with pytest.raises(DataUnavailable):
        fetch_json("T2", "https://example.test/never-cached", retries=0)


def test_timeout_is_handled(mock_http):
    def boom(req):
        raise httpx.ReadTimeout("slow", request=req)
    mock_http(boom)
    with pytest.raises(DataUnavailable) as e:
        fetch_json("T3", "https://example.test/slow", retries=1)
    assert "ReadTimeout" in e.value.reason


def test_garbage_json_is_rejected(mock_http):
    mock_http(lambda req: httpx.Response(200, text="<html>maintenance</html>"))
    with pytest.raises(DataUnavailable):
        fetch_json("T4", "https://example.test/html", retries=0)


def test_semantic_validation_rejects_error_payload(mock_http):
    mock_http(lambda req: httpx.Response(200, json={"error": True, "reason": "bad"}))

    def validate(p):
        if p.get("error"):
            raise ValueError(p["reason"])
    with pytest.raises(DataUnavailable):
        fetch_json("T5", "https://example.test/err", validate=validate, retries=0)


def test_secret_params_are_not_part_of_cache_key():
    a = fetch._cache_key("GET", "u", {"api-key": "SECRET1", "q": 1}, None)
    b = fetch._cache_key("GET", "u", {"api-key": "SECRET2", "q": 1}, None)
    assert a == b


def test_simulated_outage_blocks_without_network(mock_http):
    mock_http(lambda req: pytest.fail("network must not be called during a simulated outage"))
    fetch.simulate_outage(["open-meteo"], minutes=1)
    with pytest.raises(DataUnavailable) as e:
        fetch_json("Open-Meteo Forecast API", "https://example.test/x")
    assert "simulated" in e.value.reason
    fetch.clear_outages()
    assert fetch.active_outages() == {}


# ------------------------------------------------------------------ provider fallback

def test_weather_falls_back_to_met_norway(monkeypatch):
    def down(*a):
        raise DataUnavailable("Open-Meteo Forecast API", "HTTP 503")
    monkeypatch.setattr(weather, "_open_meteo", down)
    monkeypatch.setattr(weather, "_met_norway", lambda lat, lon: {
        "days": [], "soil": None, "provider": "MET Norway", "sources": [{"source": "MET Norway", "note": ""}]})
    out = weather.forecast(20, 74)
    assert out["provider"] == "MET Norway" and "fallback" in out["sources"][0]["note"]


def test_weather_all_providers_down_raises(monkeypatch):
    def down(*a):
        raise DataUnavailable("x", "down")
    monkeypatch.setattr(weather, "_open_meteo", down)
    monkeypatch.setattr(weather, "_met_norway", down)
    with pytest.raises(DataUnavailable):
        weather.forecast(20, 74)


# ------------------------------------------------------------------ report builder

@pytest.fixture
def offline_report(monkeypatch):
    monkeypatch.setattr(report.geocode, "reverse",
                        lambda lat, lon: {"state": "Maharashtra", "district": "Nashik", "village": "X", "source": None})

    def down(*a, **k):
        raise DataUnavailable("provider", "down")
    for mod, fn in ((report.weather, "forecast"), (report.soil, "soil_profile"), (report.satellite, "ndvi_report"),
                    (report.climate, "rainfall_anomaly"), (report.mandi, "market_summary")):
        monkeypatch.setattr(mod, fn, down)
    return monkeypatch


def test_report_survives_every_source_down(offline_report):
    rep = report.build_report(20.06, 74.13, "onion", "2026-09-15")
    assert set(rep["errors"]) == {"weather", "soil", "satellite", "rainfall", "market"}
    assert "water" not in rep            # no weather -> no invented water balance
    assert rep["risks"] == []            # no data -> no invented risks


def test_report_times_out_slow_source(offline_report):
    offline_report.setattr(report, "REPORT_BUDGET_S", 1)
    offline_report.setattr(report.satellite, "ndvi_report", lambda *a, **k: time.sleep(5))
    t = time.perf_counter()
    rep = report.build_report(20.06, 74.13, "onion", "2026-09-15")
    assert time.perf_counter() - t < 3
    assert "timed out" in rep["errors"]["satellite"]


def test_report_contains_unexpected_bug_in_one_source(offline_report):
    offline_report.setattr(report.soil, "soil_profile", lambda *a: 1 / 0)
    rep = report.build_report(20.06, 74.13, "onion", "2026-09-15")
    assert "ZeroDivisionError" in rep["errors"]["soil"]


# ------------------------------------------------------------------ agent tools

def test_tool_timeout_becomes_structured_error(monkeypatch):
    monkeypatch.setattr(tools, "TOOL_TIMEOUT_S", 1)
    monkeypatch.setitem(tools.IMPLS, "get_soil_profile", lambda ctx: time.sleep(5))
    ctx = tools.PlotContext(lat=20, lon=74, crop_id="onion", sowing_date=None)
    res = tools.execute(ctx, "get_soil_profile", "{}")
    assert res["ok"] is False and "timed out" in res["error"]


@pytest.mark.parametrize("name,args,expect", [
    ("no_such_tool", "{}", "unknown tool"),
    ("get_weather_forecast", "{not json", "invalid JSON"),
    ("get_weather_forecast", '{"plot": "x"}', "bad arguments"),
    ("get_weather_forecast", "[1, 2]", "invalid JSON"),
])
def test_bad_tool_calls_become_feedback(name, args, expect):
    ctx = tools.PlotContext(lat=20, lon=74, crop_id="onion", sowing_date=None)
    res = tools.execute(ctx, name, args)
    assert res["ok"] is False and expect in res["error"]


# ------------------------------------------------------------------ mandi trend honesty

def _row(market, modal, day="2026-10-31"):
    return {"arrival_date": day, "state": "Maharashtra", "district": "Nashik", "market": market, "commodity": "Onion",
            "variety": "Red", "grade": "FAQ", "min_price": modal, "max_price": modal, "modal_price": modal}


def test_trend_uses_only_markets_reporting_on_both_days(monkeypatch):
    today = [_row("A", 2000), _row("B", 2200), _row("C", 2400), _row("D", 9000)]
    then = [_row("A", 1000, "2026-10-24"), _row("B", 1100, "2026-10-24"), _row("C", 1200, "2026-10-24"),
            _row("E", 50, "2026-10-24")]
    monkeypatch.setattr(mandi, "live_prices", lambda s, c: (today, {"source": "live"}))
    monkeypatch.setattr(mandi, "history_on", lambda s, c, d: (then, {"source": "hist"}))
    monkeypatch.setattr(mandi.geocode, "locate_market", lambda *a: None)
    m = mandi.market_summary("Maharashtra", "Nashik", "Onion", 20, 74)
    t7 = m["trend"]["7d"]
    assert t7["markets_compared"] == 3                       # D and E excluded
    assert t7["median_then"] == 1100 and t7["median_now"] == 2200 and t7["change_pct"] == 100.0


def test_trend_omitted_when_too_few_common_markets(monkeypatch):
    monkeypatch.setattr(mandi, "live_prices", lambda s, c: ([_row("A", 2000), _row("B", 2100)], {"source": "l"}))
    monkeypatch.setattr(mandi, "history_on", lambda s, c, d: ([_row("Z", 900, "2026-10-24")], {"source": "h"}))
    monkeypatch.setattr(mandi.geocode, "locate_market", lambda *a: None)
    m = mandi.market_summary("Maharashtra", "Nashik", "Onion", 20, 74)
    assert m["trend"] == {} and m["trend_notes"]


# ------------------------------------------------------------------ LLM layer

class _FakeCompletions:
    def __init__(self, behaviour):
        self.behaviour = behaviour

    def create(self, model, **kw):
        b = self.behaviour.get(model, "ok")
        if b == "ratelimit":
            raise llm.RateLimitError("rate", response=httpx.Response(429, request=httpx.Request("POST", "u"),
                                                                     headers={"retry-after": "0"}), body=None)
        if b == "413":
            raise llm.APIStatusError("too large", response=httpx.Response(413, request=httpx.Request("POST", "u")),
                                     body=None)
        return NS(choices=[NS(message=NS(content=f"【src】 answer from {model}", tool_calls=None))])


def _pool(monkeypatch, behaviour, models):
    p = llm.LLMPool.__new__(llm.LLMPool)
    p._clients = [NS(chat=NS(completions=_FakeCompletions(behaviour)))]
    import itertools
    import threading
    p._rr, p._lock = itertools.count(), threading.Lock()
    monkeypatch.setattr(llm.settings, "groq_models", ",".join(models))
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    return p


def test_llm_falls_back_past_rate_limit_and_413(monkeypatch):
    p = _pool(monkeypatch, {"m1": "ratelimit", "m2": "413"}, ["m1", "m2", "m3"])
    notes = []
    msg, model = p.chat([{"role": "user", "content": "hi"}], notes=notes)
    assert model == "m3" and msg.content.startswith("[src]")      # CJK brackets normalised
    assert len(notes) == 2


def test_llm_all_rate_limited_waits_then_gives_up(monkeypatch):
    p = _pool(monkeypatch, {"m1": "ratelimit"}, ["m1"])
    notes = []
    with pytest.raises(llm.LLMUnavailable):
        p.chat([{"role": "user", "content": "hi"}], notes=notes)
    assert any("waiting" in n for n in notes)


def test_simulated_primary_llm_outage(monkeypatch):
    p = _pool(monkeypatch, {}, ["m1", "m2"])
    fetch.simulate_outage(["primary-llm"], minutes=1)
    try:
        notes = []
        _, model = p.chat([{"role": "user", "content": "hi"}], notes=notes)
        assert model == "m2" and "simulated" in notes[0]
    finally:
        fetch.clear_outages()
