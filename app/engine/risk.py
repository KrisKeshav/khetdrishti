"""Deterministic, explainable risk rules. Input: a field report (see engine/report.py). Output: a list of
risks, each with the exact evidence values and their source, so the UI and the agent can cite them.

Why deterministic: alerts must be reproducible and auditable; the LLM explains them, it does not invent them.
"""
from app.knowledge.crops import imd

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _ev(label, value, unit, source):
    return {"label": label, "value": value, "unit": unit, "source": source}


def assess(report: dict) -> list[dict]:
    risks: list[dict] = []
    t = imd()
    crop = report.get("crop_profile") or {}
    crop_name = crop.get("names", {}).get("en", report["plot"]["crop"])
    stage = report.get("stage") or {}
    stage_id = stage.get("stage", "unknown")
    weather = report.get("weather")
    wsrc = weather["sources"][0]["source"] if weather else ""

    if weather:
        week = weather["days"][:7]

        # 1. Heat stress — crop-specific threshold, most damaging at flowering / grain fill (mid-season).
        thr = crop.get("heat_stress_c", {}).get("value")
        if thr:
            hot = [d for d in week if d["tmax"] is not None and d["tmax"] >= thr]
            if hot:
                peak = max(hot, key=lambda d: d["tmax"])
                sev = "high" if stage_id in ("mid", "unknown") else "medium"
                risks.append({
                    "id": "heat_stress", "category": "temperature", "severity": sev,
                    "title": f"Heat stress for {crop_name}",
                    "detail": f"{len(hot)} day(s) in the next 7 reach or exceed {thr} °C, the reproductive heat-stress "
                              f"threshold for {crop_name}; peak {peak['tmax']} °C on {peak['date']}. "
                              f"Crop stage: {stage.get('stage_label', 'unknown')}.",
                    "action": "Irrigate in the evening before hot days to cool the canopy; avoid fertiliser or spraying "
                              "during peak heat.",
                    "dates": [d["date"] for d in hot],
                    "evidence": [_ev("Peak max temperature", peak["tmax"], "°C", wsrc),
                                 _ev("Crop heat-stress threshold", thr, "°C", crop["heat_stress_c"]["source"])],
                })
        severe = [d for d in week if d["tmax"] is not None and d["tmax"] >= t["severe_heat_tmax_c"]]
        if severe:
            risks.append({
                "id": "severe_heat", "category": "temperature", "severity": "high",
                "title": "Severe heat (IMD criterion)",
                "detail": f"Max temperature ≥ {t['severe_heat_tmax_c']} °C forecast on {', '.join(d['date'] for d in severe)}.",
                "action": "Protect labour and livestock from noon heat; keep the field irrigated.",
                "dates": [d["date"] for d in severe],
                "evidence": [_ev("Max temperature", max(d["tmax"] for d in severe), "°C", wsrc),
                             _ev("IMD severe heat threshold", t["severe_heat_tmax_c"], "°C", "IMD")],
            })

        # 2. Cold wave (IMD plains criterion: min temperature ≤ 4 °C).
        cold = [d for d in week if d["tmin"] is not None and d["tmin"] <= t["cold_wave_tmin_c"]]
        if cold:
            low = min(cold, key=lambda d: d["tmin"])
            risks.append({
                "id": "cold_wave", "category": "temperature",
                "severity": "high" if crop.get("season") == "rabi" else "medium",
                "title": "Cold wave / frost risk",
                "detail": f"Minimum temperature down to {low['tmin']} °C on {low['date']} (IMD cold-wave criterion "
                          f"≤ {t['cold_wave_tmin_c']} °C).",
                "action": "Light irrigation in the evening before the cold night reduces frost damage.",
                "dates": [d["date"] for d in cold],
                "evidence": [_ev("Lowest min temperature", low["tmin"], "°C", wsrc),
                             _ev("IMD cold-wave threshold", t["cold_wave_tmin_c"], "°C", "IMD")],
            })

        # 3. Heavy rain (IMD 24-h categories), and rain at harvest time.
        heavy = [d for d in week if (d["rain_mm"] or 0) >= t["rain_intensity_mm"]["heavy"]]
        if heavy:
            top = max(heavy, key=lambda d: d["rain_mm"])
            cat = ("extremely heavy" if top["rain_mm"] >= t["rain_intensity_mm"]["extremely_heavy"] else
                   "very heavy" if top["rain_mm"] >= t["rain_intensity_mm"]["very_heavy"] else "heavy")
            risks.append({
                "id": "heavy_rain", "category": "rain", "severity": "high",
                "title": f"{cat.capitalize()} rain forecast",
                "detail": f"{top['rain_mm']} mm forecast on {top['date']} — IMD category '{cat}'.",
                "action": "Clear field drainage channels; postpone fertiliser and pesticide application; "
                          "move harvested produce to cover.",
                "dates": [d["date"] for d in heavy],
                "evidence": [_ev("Max daily rain", top["rain_mm"], "mm", wsrc),
                             _ev("IMD heavy-rain threshold", t["rain_intensity_mm"]["heavy"], "mm/24h", "IMD")],
            })
        if stage_id in ("late", "after"):
            wet = [d for d in weather["days"][:5] if (d["rain_mm"] or 0) >= t["rain_intensity_mm"]["moderate"]]
            if wet:
                risks.append({
                    "id": "harvest_rain", "category": "rain", "severity": "high" if len(wet) > 1 else "medium",
                    "title": "Rain during harvest window",
                    "detail": f"Crop is in {stage.get('stage_label')}; moderate-or-heavier rain (≥ "
                              f"{t['rain_intensity_mm']['moderate']} mm) is forecast on {', '.join(d['date'] for d in wet)}.",
                    "action": "Harvest mature crop before the rain if possible, or delay cutting until after it; "
                              "keep harvested produce covered.",
                    "dates": [d["date"] for d in wet],
                    "evidence": [_ev("Rain on wettest day", max(d["rain_mm"] for d in wet), "mm", wsrc)],
                })

        # 4. Dry spell — no IMD 'rainy day' (≥ 2.5 mm) in the whole forecast horizon during active growth.
        horizon = weather["days"]
        if stage_id in ("dev", "mid") and horizon and all((d["rain_mm"] or 0) < t["rainy_day_mm"] for d in horizon):
            risks.append({
                "id": "dry_spell", "category": "water", "severity": "medium",
                "title": f"No rainy day in the next {len(horizon)} days",
                "detail": f"No day with ≥ {t['rainy_day_mm']} mm rain forecast during {stage.get('stage_label')}.",
                "action": "Plan irrigation turns now; consider mulching to conserve soil moisture.",
                "dates": [], "evidence": [_ev("Total forecast rain", round(sum(d['rain_mm'] or 0 for d in horizon), 1),
                                              "mm", wsrc)],
            })

        # 5. Spraying window (label guidance: avoid spraying in wind > 15 km/h or when rain is likely).
        next3 = weather["days"][:3]

        def spray_blockers(d):
            why = []
            if (d["wind_kmh"] or 0) >= 15:
                why.append(f"wind {d['wind_kmh']} km/h")
            if (d["rain_mm"] or 0) >= 1:
                why.append(f"rain {d['rain_mm']} mm")
            if d.get("rain_prob") is not None and d["rain_prob"] >= 40:
                why.append(f"rain chance {d['rain_prob']} %")
            return why

        good = [d["date"] for d in next3 if not spray_blockers(d)]
        blocked = "; ".join(f"{d['date']}: {', '.join(spray_blockers(d))}" for d in next3 if spray_blockers(d))
        risks.append({
            "id": "spray_window", "category": "operations", "severity": "low" if good else "medium",
            "title": "Spraying window" if good else "No safe spraying window in next 3 days",
            "detail": (f"Suitable days for spraying (wind < 15 km/h, rain chance < 40 %): {', '.join(good)}."
                       + (f" Not suitable — {blocked}." if blocked else "")
                       if good else f"Not suitable — {blocked}."),
            "action": "Spray early morning or late evening on the suitable day." if good else
                      "Postpone spraying; rain would wash it off and wind causes drift.",
            "dates": good,
            "evidence": [_ev(f"Wind {d['date']}", d["wind_kmh"], "km/h", wsrc) for d in next3],
        })

        # 6. Fungal-disease weather: ≥ 3 consecutive humid days (RH ≥ 85 %) with mild temperatures.
        streak, best = 0, 0
        for d in week:
            mild = d["tmax"] is not None and 20 <= (d["tmax"] + d["tmin"]) / 2 <= 30
            streak = streak + 1 if (d.get("rh_mean") or 0) >= 85 and mild else 0
            best = max(best, streak)
        if best >= 3:
            risks.append({
                "id": "disease_weather", "category": "pest_disease", "severity": "medium",
                "title": "Weather favours fungal disease",
                "detail": f"{best} consecutive humid days (mean RH ≥ 85 %, mild temperatures) forecast.",
                "action": "Scout the crop for leaf spots/blight; ensure field drainage. Consult the local KVK "
                          "before using any fungicide.",
                "dates": [], "evidence": [_ev("Consecutive humid days", best, "days", wsrc)],
                "basis": "general plant-pathology guidance: prolonged leaf wetness favours fungal infection",
            })

    # 7. Irrigation scheduling from the FAO-56 water balance.
    wb = report.get("water")
    if wb and wb.get("soil_moisture_known"):
        rec = wb["recommendation"]
        ev = [_ev("Available soil water (0-27 cm)", wb["available_water_pct"], "%", "Open-Meteo soil model + SoilGrids"),
              _ev("Crop water use next 7 days", wb["crop_water_use_mm"], "mm", "FAO-56 Kc × ET0"),
              _ev("Effective rain next 3 days", wb["effective_rain_next_3d_mm"], "mm", wsrc)]
        if rec == "irrigate_now":
            risks.append({"id": "irrigate_now", "category": "water", "severity": "high",
                          "title": "Irrigation needed now",
                          "detail": f"Root-zone water is depleted beyond the crop's stress point and little rain is "
                                    f"expected in 3 days.",
                          "action": f"Irrigate about {wb['suggested_irrigation_mm']} mm now.",
                          "dates": [], "evidence": ev})
        elif rec == "irrigate_on_due_date":
            risks.append({"id": "irrigate_soon", "category": "water", "severity": "medium",
                          "title": f"Irrigation due on {wb['irrigation_due_date']}",
                          "detail": "Soil water will fall below the crop's stress point on that date at the forecast "
                                    "rate of crop water use.",
                          "action": f"Plan about {wb['suggested_irrigation_mm']} mm on {wb['irrigation_due_date']}.",
                          "dates": [wb["irrigation_due_date"]], "evidence": ev})
        elif rec == "wait_rain_expected":
            risks.append({"id": "skip_irrigation", "category": "water", "severity": "low",
                          "title": "Hold irrigation — rain expected",
                          "detail": "Soil is dry, but forecast rain in the next 3 days covers at least half of the deficit.",
                          "action": "Skip irrigation and re-check after the rain.", "dates": [], "evidence": ev})

    # 8. Crop vigour from satellite NDVI.
    sat = report.get("satellite")
    if sat:
        ssrc = sat["sources"][0]["source"]
        chg = sat.get("change_vs_last_year_pct")
        if chg is not None and chg <= -20:
            risks.append({"id": "ndvi_below_last_year", "category": "crop_health", "severity": "medium",
                          "title": "Crop greenness well below last year",
                          "detail": f"NDVI {sat['latest_ndvi']} on {sat['latest_date']} vs {sat['last_year_ndvi']} "
                                    f"around the same date last year ({chg} %). {sat.get('last_year_note', '')}",
                          "action": "Walk the field to check for water stress, nutrient deficiency, pests or patchy "
                                    "germination.",
                          "dates": [], "evidence": [_ev("NDVI now", sat["latest_ndvi"], "", ssrc),
                                                    _ev("NDVI last year", sat["last_year_ndvi"], "", ssrc)]})
        d30 = sat.get("ndvi_change_30d")
        if d30 is not None and d30 <= -0.1 and stage_id in ("dev", "mid", "unknown"):
            risks.append({"id": "ndvi_declining", "category": "crop_health", "severity": "medium",
                          "title": "Crop greenness falling",
                          "detail": f"NDVI dropped by {abs(d30)} since {sat['ndvi_change_30d_ref_date']} during a stage "
                                    f"when it should be stable or rising.",
                          "action": "Inspect for water stress, pest or disease damage.",
                          "dates": [], "evidence": [_ev("NDVI 30-day change", d30, "", ssrc)]})

    # 9. Rainfall anomaly (IMD categories).
    rf = report.get("rainfall")
    if rf and rf.get("departure_pct") is not None and rf["imd_category"] not in ("normal",):
        dep = rf["departure_pct"]
        risks.append({
            "id": "rain_anomaly", "category": "climate",
            "severity": "medium" if rf["imd_category"] in ("deficient", "excess") else "high",
            "title": f"Last {rf['window_days']} days: {rf['imd_category']} rainfall",
            "detail": f"{rf['rain_mm']} mm vs a {rf['baseline_years']}-year normal of {rf['normal_mm']} mm "
                      f"({dep:+d} %), {rf['window_start']} to {rf['window_end']}.",
            "action": ("Expect lower soil moisture reserves; prioritise irrigation at sensitive stages."
                       if dep < 0 else "Watch for waterlogging and disease; ensure drainage."),
            "dates": [], "evidence": [_ev("Rain in window", rf["rain_mm"], "mm", rf["sources"][0]["source"]),
                                      _ev("10-year normal", rf["normal_mm"], "mm", rf["sources"][0]["source"])]})

    # 10. Market movement (informational).
    mk = report.get("market")
    if mk and "7d" in mk.get("trend", {}):
        tr = mk["trend"]["7d"]
        ch = tr["change_pct"]
        if abs(ch) >= 5:
            risks.append({
                "id": "price_move", "category": "market", "severity": "low",
                "title": f"{mk['commodity']} price {'up' if ch > 0 else 'down'} {abs(ch)} % in 7 days",
                "detail": f"Median modal price across the {tr['markets_compared']} markets that reported on both days: "
                          f"Rs {tr['median_now']}/quintal on {mk['arrival_date']} vs Rs {tr['median_then']} on {tr['date']}.",
                "action": "Compare nearby mandis before selling.",
                "dates": [], "evidence": [_ev("Median modal price now", tr["median_now"], "Rs/quintal",
                                              mk["sources"][0]["source"]),
                                          _ev("Markets compared", tr["markets_compared"], "", "Agmarknet")]})

    risks.sort(key=lambda r: SEVERITY_ORDER[r["severity"]])
    return risks
