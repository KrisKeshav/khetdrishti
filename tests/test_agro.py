"""Unit tests for pure agronomy maths. Inputs here are test fixtures only (never used by the app)."""
from datetime import date

import pytest

from app.engine.agro import (effective_rain, extraterrestrial_radiation, hargreaves_et0, usda_texture,
                             water_balance)
from app.knowledge.crops import crop_stage, get_crop, kc_on_day


def test_extraterrestrial_radiation_matches_fao56_example():
    # FAO-56 Example 8: 20°S on 3 September (day 246) -> Ra = 32.2 MJ m-2 day-1
    assert extraterrestrial_radiation(-20, 246) == pytest.approx(32.2, abs=0.2)


def test_hargreaves_is_plausible_for_indian_summer():
    et0 = hargreaves_et0(40, 25, 22, 135)
    assert 6 < et0 < 9


@pytest.mark.parametrize("sand,silt,clay,expected", [
    (92, 4, 4, "sand"), (40, 40, 20, "loam"), (20, 65, 15, "silt loam"), (20, 30, 50, "clay"),
    (65, 15, 20, "sandy clay loam"), (5, 50, 45, "silty clay"), (30, 35, 35, "clay loam"),
])
def test_usda_texture(sand, silt, clay, expected):
    assert usda_texture(sand, silt, clay) == expected


def test_effective_rain_ignores_light_showers():
    assert effective_rain(0.5, 5) == 0
    assert effective_rain(10, 5) == pytest.approx(8)


def test_kc_curve_follows_fao_shape():
    crop = get_crop("wheat")
    s = crop["stages_days"]
    assert kc_on_day(crop, 0) == (crop["kc"]["ini"], "ini")
    assert kc_on_day(crop, s["ini"] + s["dev"] + 1)[0] == crop["kc"]["mid"]
    mid_dev, stage = kc_on_day(crop, s["ini"] + s["dev"] // 2)
    assert stage == "dev" and crop["kc"]["ini"] < mid_dev < crop["kc"]["mid"]


def test_crop_stage_without_sowing_date_is_flagged():
    st = crop_stage("soybean", None, today=date(2026, 8, 1))
    assert st["known"] is False and "assumption" in st


def test_crop_stage_rejects_future_sowing():
    with pytest.raises(ValueError):
        crop_stage("soybean", "2026-12-01", today=date(2026, 8, 1))


def _days(rain, et0=5.0, n=7):
    return [{"date": f"2026-08-{i + 1:02d}", "rain_mm": rain[i] if i < len(rain) else 0, "et0_mm": et0} for i in range(n)]


def test_water_balance_irrigate_now_when_dry_and_no_rain():
    wb = water_balance(_days([0] * 7), {}, theta_now=0.14, fc=0.25, wp=0.12, p=0.5)
    assert wb["recommendation"] == "irrigate_now"
    assert wb["suggested_irrigation_mm"] > 0


def test_water_balance_waits_for_rain():
    wb = water_balance(_days([40, 30, 0]), {}, theta_now=0.14, fc=0.25, wp=0.12, p=0.5)
    assert wb["recommendation"] == "wait_rain_expected"


def test_water_balance_wet_soil_needs_nothing():
    wb = water_balance(_days([0] * 7, et0=1.0), {}, theta_now=0.25, fc=0.25, wp=0.12, p=0.5)
    assert wb["recommendation"] == "no_irrigation_needed"


def test_water_balance_without_soil_moisture_does_not_invent_it():
    wb = water_balance(_days([0] * 7), {}, theta_now=None, fc=0.25, wp=0.12, p=0.5)
    assert wb["recommendation"] == "soil_moisture_unavailable"
    assert "available_water_pct" not in wb


def test_season_warning_flags_unlikely_sowing_month():
    from app.knowledge.crops import season_warning
    assert season_warning(get_crop("chickpea"), date(2026, 5, 31))          # rabi crop sown in May
    assert season_warning(get_crop("chickpea"), date(2026, 10, 25)) is None
    assert season_warning(get_crop("soybean"), date(2026, 6, 20)) is None   # kharif in June
    assert season_warning(get_crop("tomato"), date(2026, 3, 1)) is None     # 'any' season: never flagged
