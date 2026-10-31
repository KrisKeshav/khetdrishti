"""Proactive monitoring: the autonomous part of the system.

Every day (and on demand from the UI), for every registered plot:
  build the live field report -> run the risk engine -> store new medium/high risks as alerts
  -> push each new alert to the farmer on Telegram (if configured).
Deduplication: the same risk for the same plot is raised at most once per day.
"""
from datetime import date

import httpx

from app.config import settings
from app.db import list_plots, mark_notified, retract_alerts, save_alert
from app.engine.report import BadRequest, build_report

TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"
SEV_ICON = {"high": "🔴", "medium": "🟠", "low": "🟢"}


def run_monitor() -> dict:
    today = date.today().isoformat()
    summary = {"date": today, "plots": 0, "new_alerts": 0, "retracted": 0, "notified": 0, "errors": [], "details": []}
    for plot in list_plots():
        summary["plots"] += 1
        try:
            rep = build_report(plot["lat"], plot["lon"], plot["crop"], plot["sowing_date"])
        except BadRequest as e:
            summary["errors"].append(f"plot {plot['id']}: {e}")
            continue
        new = []
        active = {r["id"] for r in rep["risks"] if r["severity"] != "low"}
        for risk in rep["risks"]:
            if risk["severity"] == "low":
                continue
            if save_alert(plot["id"], risk, today):
                new.append(risk)
        summary["retracted"] = summary.get("retracted", 0) + retract_alerts(plot["id"], today, active)
        summary["new_alerts"] += len(new)
        chat_id = plot.get("telegram_chat_id") or settings.telegram_default_chat_id
        for risk in new:
            if chat_id and settings.telegram_bot_token and _send_telegram(chat_id, plot, risk, rep):
                mark_notified(plot["id"], risk["id"], today)
                summary["notified"] += 1
        summary["details"].append({"plot_id": plot["id"], "name": plot["name"],
                                   "risks": [r["id"] for r in rep["risks"]],
                                   "new_alerts": [r["id"] for r in new], "source_errors": rep["errors"]})
    return summary


def _send_telegram(chat_id: str, plot: dict, risk: dict, rep: dict) -> bool:
    ev = "\n".join(f"• {e['label']}: {e['value']} {e['unit']} ({e['source']})" for e in risk["evidence"][:3])
    text = (f"{SEV_ICON[risk['severity']]} KhetDrishti alert — {plot['name'] or 'your plot'} ({plot['crop']})\n\n"
            f"{risk['title']}\n{risk['detail']}\n\n✅ {risk['action']}\n\nEvidence:\n{ev}")
    try:
        r = httpx.post(TELEGRAM_URL.format(token=settings.telegram_bot_token),
                       json={"chat_id": chat_id, "text": text[:4000]}, timeout=15)
        return r.status_code == 200
    except httpx.HTTPError:
        return False
