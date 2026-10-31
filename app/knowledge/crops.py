"""Crop profiles (from data/crop_profiles.yaml) and crop-stage maths."""
from datetime import date, timedelta
from functools import lru_cache

import yaml

from app.config import DATA_DIR

STAGE_LABELS = {
    "ini": "initial (establishment)",
    "dev": "development (vegetative growth)",
    "mid": "mid-season (flowering / grain or fruit filling)",
    "late": "late season (maturity / harvest)",
}


@lru_cache
def _profiles() -> dict:
    with open(DATA_DIR / "crop_profiles.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def crops() -> dict:
    return _profiles()["crops"]


def imd() -> dict:
    return _profiles()["imd"]


def citation(key: str) -> str:
    return _profiles()["sources"].get(key, key)


def get_crop(name: str) -> dict | None:
    return crops().get((name or "").strip().lower())


def crop_list() -> list[dict]:
    return [{"id": k, "names": v["names"], "season": v["season"]} for k, v in crops().items()]


# Generous sowing windows for India's two main seasons (kharif ~Jun-Jul, rabi ~Oct-Nov), padded by a month or
# more each side so late/early sowing is not flagged. Used only to warn about likely data-entry mistakes.
SOWING_MONTHS = {"kharif": {5, 6, 7, 8, 9}, "rabi": {9, 10, 11, 12, 1}}


def season_warning(crop: dict, sowing: date) -> str | None:
    months = SOWING_MONTHS.get(crop.get("season", ""))
    if months and sowing.month not in months:
        return (f"{crop['names']['en']} is a {crop['season']} crop, usually not sown in {sowing.strftime('%B')}. "
                "Please check the sowing date.")
    return None


def kc_on_day(crop: dict, day: int) -> tuple[float, str]:
    """FAO-56 Kc curve: flat in initial stage, linear rise in development, flat mid-season,
    linear decline in late season. `day` = days after sowing (0-based)."""
    s = crop["stages_days"]
    kc = crop["kc"]
    ini, dev, mid, late = s["ini"], s["dev"], s["mid"], s["late"]
    if day < ini:
        return kc["ini"], "ini"
    if day < ini + dev:
        frac = (day - ini) / dev
        return kc["ini"] + frac * (kc["mid"] - kc["ini"]), "dev"
    if day < ini + dev + mid:
        return kc["mid"], "mid"
    if day < ini + dev + mid + late:
        frac = (day - ini - dev - mid) / late
        return kc["mid"] + frac * (kc["end"] - kc["mid"]), "late"
    return kc["end"], "after"


def crop_stage(crop_id: str, sowing_date: str | None, today: date | None = None) -> dict:
    """Current stage + per-day Kc for the next 16 days. Without a sowing date we cannot know the stage,
    so we say so and use mid-season Kc (the conservative, highest-water-demand assumption)."""
    crop = get_crop(crop_id)
    if crop is None:
        raise KeyError(crop_id)
    today = today or date.today()
    s = crop["stages_days"]
    season_len = s["ini"] + s["dev"] + s["mid"] + s["late"]
    if not sowing_date:
        kc_by_date = {(today + timedelta(days=i)).isoformat(): crop["kc"]["mid"] for i in range(16)}
        return {"known": False, "stage": "unknown", "stage_label": "unknown (no sowing date given)",
                "kc_today": crop["kc"]["mid"], "kc_by_date": kc_by_date, "season_length_days": season_len,
                "assumption": "sowing date not provided — using mid-season Kc (highest water demand)"}
    sown = date.fromisoformat(sowing_date)
    das = (today - sown).days
    if das < 0:
        raise ValueError("Sowing date is in the future.")
    kc_by_date = {}
    for i in range(16):
        kc, _ = kc_on_day(crop, das + i)
        kc_by_date[(today + timedelta(days=i)).isoformat()] = round(kc, 2)
    kc_today, stage = kc_on_day(crop, das)
    stage_start = {"ini": 0, "dev": s["ini"], "mid": s["ini"] + s["dev"],
                   "late": s["ini"] + s["dev"] + s["mid"], "after": season_len}[stage]
    return {
        "known": True,
        "days_after_sowing": das,
        "stage": stage,
        "stage_label": STAGE_LABELS.get(stage, "past typical season length (harvest due/over)"),
        "day_in_stage": das - stage_start,
        "kc_today": round(kc_today, 2),
        "kc_by_date": kc_by_date,
        "season_length_days": season_len,
        "days_to_typical_harvest": max(season_len - das, 0),
        "typical_harvest_date": (sown + timedelta(days=season_len)).isoformat(),
        "stage_source": s["source"],
        "season_warning": season_warning(crop, sown),
    }
