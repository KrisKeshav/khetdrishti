"""Risk-rule tests on synthetic report fixtures (tests only — never imported by the app)."""
from app.engine.risk import assess
from app.knowledge.crops import get_crop

SRC = [{"source": "test fixture"}]


def report(days, stage="mid", crop="wheat", **extra):
    return {"plot": {"crop": crop}, "crop_profile": get_crop(crop), "stage": {"stage": stage, "stage_label": stage},
            "weather": {"days": days, "sources": SRC}, **extra}


def day(i, tmax=30, tmin=18, rain=0, wind=8, prob=10, rh=60):
    return {"date": f"2026-03-{i + 1:02d}", "tmax": tmax, "tmin": tmin, "rain_mm": rain, "rain_prob": prob,
            "wind_kmh": wind, "rh_mean": rh}


def ids(risks):
    return {r["id"] for r in risks}


def test_heat_stress_at_flowering_is_high():
    risks = assess(report([day(0, tmax=36)] + [day(i) for i in range(1, 7)]))
    heat = next(r for r in risks if r["id"] == "heat_stress")
    assert heat["severity"] == "high"
    assert any(e["value"] == 36 for e in heat["evidence"])


def test_heavy_rain_uses_imd_threshold():
    assert "heavy_rain" in ids(assess(report([day(0, rain=70)] + [day(i) for i in range(1, 7)])))
    assert "heavy_rain" not in ids(assess(report([day(0, rain=60)] + [day(i) for i in range(1, 7)])))


def test_cold_wave_for_rabi_crop():
    risks = assess(report([day(0, tmin=3)] + [day(i) for i in range(1, 7)]))
    assert next(r for r in risks if r["id"] == "cold_wave")["severity"] == "high"


def test_spray_window_lists_good_days_and_reasons():
    risks = assess(report([day(0, wind=20), day(1), day(2, prob=80)] + [day(i) for i in range(3, 7)]))
    spray = next(r for r in risks if r["id"] == "spray_window")
    assert spray["dates"] == ["2026-03-02"]
    assert "wind 20" in spray["detail"]


def test_missing_sources_produce_no_invented_risks():
    rep = {"plot": {"crop": "wheat"}, "crop_profile": get_crop("wheat"), "stage": {"stage": "mid"}}
    assert assess(rep) == []


def test_risks_sorted_by_severity():
    risks = assess(report([day(0, tmax=46, rain=120)] + [day(i) for i in range(1, 7)]))
    order = {"high": 0, "medium": 1, "low": 2}
    assert [order[r["severity"]] for r in risks] == sorted(order[r["severity"]] for r in risks)
