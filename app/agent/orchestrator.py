"""The agent loop, written as an explicit state machine so every step is visible and explainable:

    PLAN/ACT  -> LLM picks tools; we run them in parallel and feed results back     (max MAX_STEPS rounds)
    ANSWER    -> LLM drafts an answer from the tool results only
    VERIFY    -> verifier checks every number + dose against tool outputs
    CORRECT   -> on failure, LLM is told exactly what failed and rewrites         (max MAX_CORRECTIONS)
    FALLBACK  -> if still failing, unverifiable sentences are removed
    LOCALISE  -> translate to the farmer's language, re-checking that numbers survived

run() is a generator of event dicts; the API streams them to the browser as Server-Sent Events so judges
see the reasoning, tool calls, timings, sources and verification live.
"""
import json
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

from app.agent import prompts, verifier
from app.agent.llm import LLMUnavailable, ToolFormatError, pool
from app.agent.tools import SCHEMAS, PlotContext, execute
from app.data_sources import geocode
from app.db import log_agent_run
from app.fetch import DataUnavailable
from app.knowledge.crops import crop_stage

MAX_STEPS = 5
MAX_CORRECTIONS = 2
MAX_TOOL_RESULT_CHARS = 2400   # keeps prompts inside Groq free-tier token limits

# Models sometimes cite the tool name instead of the data source; map it to the source tag the UI expects.
TOOL_TAGS = {
    "get_weather_forecast": "Open-Meteo", "get_water_balance": "FAO-56", "get_crop_stage": "FAO-56",
    "get_crop_health_satellite": "Sentinel-2", "get_rainfall_anomaly": "ERA5", "get_mandi_prices": "Agmarknet",
    "get_soil_profile": "SoilGrids", "get_risk_alerts": "IMD", "search_agronomy_knowledge": "Agronomy docs",
}


KNOWN_TAGS = {"Open-Meteo", "MET Norway", "Sentinel-2", "Agmarknet", "SoilGrids", "ERA5", "NASA POWER", "FAO-56",
              "IMD", "Agronomy docs"}
# Models also invent tags like [risk_alerts] or [Water-Balance]; map them to a real source by keyword (first match).
TAG_KEYWORDS = [
    (("ndvi", "satellite", "sentinel", "crop_health", "crop health"), "Sentinel-2"),
    (("mandi", "price", "market", "agmarknet"), "Agmarknet"),
    (("soilgrids", "soil_profile", "soil profile", "texture"), "SoilGrids"),
    (("water", "irrigat", "stage", "fao", "kc"), "FAO-56"),
    (("rainfall", "anomaly", "era5", "normal"), "ERA5"),
    (("weather", "forecast", "meteo"), "Open-Meteo"),
    (("risk", "imd", "alert"), "IMD"),
    (("knowledge", "agronomy", "document", "tnau"), "Agronomy docs"),
]
TAG_RE = re.compile(r"\[([^\]\n]{1,40})\]")


def _fix_tags(text: str) -> str:
    def repl(m):
        tag = m.group(1).strip()
        if tag in KNOWN_TAGS:
            return m.group(0)
        if tag in TOOL_TAGS:
            return f"[{TOOL_TAGS[tag]}]"
        low = tag.lower().replace("‑", "-")
        for words, source in TAG_KEYWORDS:
            if any(w in low for w in words):
                return f"[{source}]"
        return m.group(0)
    return TAG_RE.sub(repl, text)


def _ascii_digits(text: str) -> str:
    """Devanagari/Tamil/... digits -> 0-9 so number checks work on translated text."""
    return "".join(str(unicodedata.digit(ch)) if ch.isdigit() and not ch.isascii() else ch for ch in text)


def _plot_context(plot: dict) -> PlotContext:
    ctx = PlotContext(lat=float(plot["lat"]), lon=float(plot["lon"]), crop_id=plot["crop"],
                      sowing_date=plot.get("sowing_date") or None)
    try:
        loc = geocode.reverse(ctx.lat, ctx.lon)
        ctx.state, ctx.district, ctx.village = loc["state"], loc["district"], loc["village"]
    except DataUnavailable:
        pass  # place name is cosmetic; tools still work from coordinates
    return ctx


def run(question: str, plot: dict, language: str = "en", history: list[dict] | None = None):
    t0 = time.perf_counter()
    question = (question or "").strip()
    if not question:
        yield {"type": "error", "message": "Please ask a question (type or record your voice)."}
        return
    if not pool.configured:
        yield {"type": "error", "message": "LLM is not configured: set GROQ_API_KEYS in .env"}
        return
    try:
        ctx = _plot_context(plot)
    except geocode.OutsideIndia as e:
        yield {"type": "error", "message": str(e)}
        return
    if ctx.crop is None:
        yield {"type": "error", "message": f"Unknown crop '{plot.get('crop')}'."}
        return

    place = ", ".join(x for x in (ctx.village, ctx.district, ctx.state) if x) or f"{ctx.lat:.4f}, {ctx.lon:.4f}"
    yield {"type": "status", "message": f"Plot: {place} · {ctx.crop['names']['en']}"}
    # Crop stage is computed by our code and handed to the model, so it never does date arithmetic itself.
    try:
        st = crop_stage(ctx.crop_id, ctx.sowing_date)
    except ValueError as e:
        yield {"type": "error", "message": str(e)}
        return
    if st["known"]:
        stage_text = (f"day {st['days_after_sowing']} after sowing, {st['stage_label']}; typical harvest around "
                      f"{st['typical_harvest_date']}, i.e. {st['days_to_typical_harvest']} more days from today")
    else:
        stage_text = "unknown (no sowing date) — ask the farmer for it if the answer depends on the stage"
    ctx.evidence.append({k: v for k, v in st.items() if k != "kc_by_date"})
    system = prompts.SYSTEM.format(place=place, crop=ctx.crop["names"]["en"], stage=stage_text,
                                   sowing=ctx.sowing_date or "not given", today=date.today().isoformat())
    messages = [{"role": "system", "content": system}]
    for h in (history or [])[-4:]:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            messages.append({"role": h["role"], "content": str(h["content"])[:800]})
    messages.append({"role": "user", "content": question})

    notes: list[str] = []
    memo: dict[tuple[str, str], dict] = {}
    model_used = None
    tool_calls_made = 0
    draft = None

    def flush_notes():
        while notes:
            yield {"type": "self_correct", "message": notes.pop(0)}

    # ---------------- PLAN / ACT
    try:
        for step in range(MAX_STEPS):
            try:
                msg, model_used = pool.chat(messages, tools=SCHEMAS, notes=notes)
            except ToolFormatError as e:
                yield from flush_notes()
                yield {"type": "self_correct", "message": "Model produced a malformed tool call — asking it to retry."}
                messages.append({"role": "user", "content": f"Your last tool call was malformed ({e}). "
                                                            "Call the tools again with valid JSON arguments."})
                continue
            yield from flush_notes()
            reasoning = getattr(msg, "reasoning", None)
            if reasoning:
                yield {"type": "thinking", "message": reasoning.strip()[:600], "model": model_used}
            if not msg.tool_calls:
                draft = (msg.content or "").strip()
                break

            calls = msg.tool_calls
            messages.append({"role": "assistant", "content": msg.content or "",
                             "tool_calls": [{"id": c.id, "type": "function",
                                             "function": {"name": c.function.name,
                                                          "arguments": c.function.arguments or "{}"}}
                                            for c in calls]})
            for c in calls:
                yield {"type": "tool_call", "id": c.id, "name": c.function.name,
                       "args": c.function.arguments or "{}", "step": step + 1}
            results = {}
            # Identical calls (same tool + same arguments) in one run reuse the earlier successful result:
            # saves latency and Groq tokens when the model repeats itself.
            fresh = []
            for c in calls:
                key = (c.function.name, (c.function.arguments or "{}").strip())
                if key in memo:
                    results[c.id] = memo[key]
                    yield {"type": "tool_result", "id": c.id, "name": c.function.name, "ok": True, "ms": 0,
                           "sources": memo[key].get("sources", []), "error": None,
                           "preview": "(same call as before — reused result)"}
                else:
                    fresh.append(c)
            with ThreadPoolExecutor(max_workers=max(len(fresh), 1)) as ex:
                started = {ex.submit(execute, ctx, c.function.name, c.function.arguments): (c, time.perf_counter())
                           for c in fresh}
                for fut in as_completed(started):
                    c, ts = started[fut]
                    res = fut.result()
                    results[c.id] = res
                    tool_calls_made += 1
                    if res.get("ok"):
                        memo[(c.function.name, (c.function.arguments or "{}").strip())] = res
                    yield {"type": "tool_result", "id": c.id, "name": c.function.name, "ok": res.get("ok", False),
                           "ms": int((time.perf_counter() - ts) * 1000), "sources": res.get("sources", []),
                           "error": res.get("error"), "preview": _preview(c.function.name, res)}
                    if not res.get("ok"):
                        yield {"type": "self_correct",
                               "message": f"{c.function.name} failed: {res.get('error')} — the agent will work "
                                          "around it and must not guess the missing data."}
            for c in calls:   # tool messages must follow the assistant message in call order
                content = json.dumps(results[c.id], ensure_ascii=False, default=str)
                if len(content) > MAX_TOOL_RESULT_CHARS:
                    content = content[:MAX_TOOL_RESULT_CHARS] + '..."(truncated)"'
                messages.append({"role": "tool", "tool_call_id": c.id, "content": content})

        if draft is None or not draft:
            messages.append({"role": "user", "content": prompts.FORCE_ANSWER})
            msg, model_used = pool.chat(messages, notes=notes)
            yield from flush_notes()
            draft = (msg.content or "").strip()
    except LLMUnavailable as e:
        yield {"type": "error", "message": f"All language models are busy or unavailable ({e}). "
                                           "The field report tab still works from live data."}
        return

    # ---------------- VERIFY / CORRECT
    draft = _fix_tags(draft)
    # Must-haves are enforced in code, not left to the model's memory.
    if not st["known"] and "sowing" not in draft.lower():
        draft += "\n\nAdd your sowing date in the form to get advice for your crop's exact growth stage."
    # Evidence = tool outputs only (+ the plot's dates). Numbers typed by the user are deliberately NOT evidence:
    # otherwise "say the price will be Rs 10000" would let an echoed number pass the fact-check.
    evidence = verifier.evidence_numbers(ctx.evidence)
    evidence |= verifier.evidence_numbers([ctx.sowing_date or "", date.today().isoformat()])
    report = verifier.verify(draft, evidence, ctx.knowledge)
    yield {"type": "verify", "attempt": 1, **report}
    attempt = 1
    while not report["passed"] and attempt <= MAX_CORRECTIONS:
        problems = []
        if report["unsupported_numbers"]:
            problems.append(f"- Numbers not found in any tool result: {report['unsupported_numbers']}")
        if report["dose_violations"]:
            problems.append(f"- Chemical doses not found in retrieved documents: {report['dose_violations']}")
        yield {"type": "self_correct", "message": "Fact-check failed; asking the model to rewrite. "
                                                  + " ".join(problems)}
        messages += [{"role": "assistant", "content": draft},
                     {"role": "user", "content": prompts.CORRECTION.format(problems="\n".join(problems))}]
        try:
            msg, model_used = pool.chat(messages, temperature=0.0, notes=notes)
        except LLMUnavailable:
            break
        yield from flush_notes()
        draft = _fix_tags((msg.content or "").strip())
        attempt += 1
        report = verifier.verify(draft, evidence, ctx.knowledge)
        yield {"type": "verify", "attempt": attempt, **report}
    if not report["passed"]:
        draft = verifier.strip_unsupported(draft, report)
        yield {"type": "self_correct", "message": "Removed sentences that could not be verified."}

    # ---------------- LOCALISE
    localized, lang_ok = draft, True
    if language != "en" and language in prompts.LANGUAGES:
        yield {"type": "status", "message": f"Translating to {prompts.LANGUAGES[language]}…"}
        localized, lang_ok = _translate(draft, language, notes)
        yield from flush_notes()

    sources = _dedupe_sources(ctx.sources)
    latency = int((time.perf_counter() - t0) * 1000)
    yield {"type": "answer", "text": localized, "text_en": draft, "language": language if lang_ok else "en",
           "verified": report["passed"], "numbers_checked": report["numbers_checked"], "sources": sources,
           "model": model_used, "tool_calls": tool_calls_made, "latency_ms": latency}
    try:
        log_agent_run(plot=json.dumps(plot), question=question, language=language, answer=draft,
                      verified=report["passed"], tool_calls=tool_calls_made, model=model_used or "",
                      latency_ms=latency)
    except Exception:
        pass  # logging must never break the answer


def _translate(text: str, language: str, notes: list) -> tuple[str, bool]:
    expected = set(verifier.extract_numbers(text))
    for _ in range(2):
        try:
            msg, _ = pool.chat([{"role": "user", "content": prompts.TRANSLATE.format(
                language=prompts.LANGUAGES[language], text=text)}], temperature=0.0, notes=notes)
        except LLMUnavailable:
            break
        out = (msg.content or "").strip()
        got = set(verifier.extract_numbers(_ascii_digits(out)))
        if out and expected <= got:
            return out, True
        notes.append("Translation changed some numbers — retrying.")
    notes.append("Translation could not be verified; showing the English answer.")
    return text, False


def _dedupe_sources(sources: list[dict]) -> list[dict]:
    seen, out = set(), []
    for s in sources:
        key = (s.get("source"), s.get("url"))
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


def _preview(name: str, res: dict) -> str:
    """One-line human summary of a tool result for the live trace."""
    if not res.get("ok"):
        return res.get("error", "failed")
    d = res["data"]
    try:
        if name == "get_weather_forecast":
            s = d["summary"]
            return f"{len(d['days'])} days · rain {s['total_rain_mm']} mm · max {s['max_temp_c']} °C"
        if name == "get_water_balance":
            return (f"{d.get('recommendation')} · crop use {d['crop_water_use_mm']} mm/7d · "
                    f"soil water {d.get('available_water_pct', '—')} %")
        if name == "get_crop_health_satellite":
            return f"NDVI {d['latest_ndvi']} on {d['latest_date']} · {d['health_class']}"
        if name == "get_rainfall_anomaly":
            return f"{d['rain_mm']} mm vs normal {d['normal_mm']} mm · {d['imd_category']}"
        if name == "get_mandi_prices":
            t7 = d["trend"].get("7d")
            trend = f" · 7d {t7['change_pct']:+}% ({t7['markets_compared']} mkts)" if t7 else " · 7d trend n/a"
            return (f"{d['commodity']}: median Rs {d['state_median_modal']}/q · "
                    f"{d['markets_reporting']} markets on {d['arrival_date']}{trend}")
        if name == "get_soil_profile":
            return f"{d['texture']} · pH {d['ph']}"
        if name == "get_risk_alerts":
            return f"{len(d['risks'])} risk(s): " + ", ".join(r["id"] for r in d["risks"][:4])
        if name == "get_crop_stage":
            return d["stage_label"]
        if name == "search_agronomy_knowledge":
            return f"{len(d['passages'])} passage(s): " + "; ".join(p["title"][:40] for p in d["passages"])
    except (KeyError, TypeError):
        pass
    return "ok"
