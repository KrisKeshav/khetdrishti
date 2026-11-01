# Forge '26 submission — copy-paste text

Theme: **Climate, Resource Infrastructure & Global Supply Resilience**
Each block below is plain text, ready to paste into the matching form field.

---

## Project name

```
KhetDrishti — plot-level climate-risk AI agent for Indian farmers
```

---

## Problem Statement

```
India's farms are small and its weather is getting harder to predict. 86% of land holdings are small or marginal (Agriculture Census 2015-16), and these farmers make high-stakes decisions every week — irrigate or wait, spray or hold, sell now or later — on generic, district-level advisories and word of mouth.

The data needed for better decisions already exists, but it is scattered and unusable for a farmer:
• Satellite imagery shows how a specific field is growing, but nobody turns it into advice.
• Weather and soil-moisture forecasts are global datasets with no crop- or stage-specific meaning.
• Mandi prices are published daily on data.gov.in, but not compared across nearby markets.
• Advice arrives in English text, while many farmers prefer to speak and listen in their own language.

The climate signal is real. In late October 2026, KhetDrishti measured 30-day rainfall 95% below the 10-year normal for a plot near Niphad, Nashik (IMD category: large deficient), and 91% below normal near Chhindwara. Farmers there have to decide whether to irrigate a newly sown rabi crop, whether to spray, and where to sell.

Generic AI chatbots don't solve this. They don't know the farmer's field, they confidently make up numbers, and they wait to be asked. Where a wrong number can mean wasted water, a lost crop or a bad sale, advice has to be specific to the plot, grounded in live data, verifiable and proactive.

Target audience: small and marginal farmers, and the Krishi Vigyan Kendra (KVK) extension workers and FPOs (farmer producer organisations) who advise hundreds of plots each.
```

---

## Solution Description

```
KhetDrishti ("field vision") is a voice-first, multilingual AI agent that gives advice for ONE specific farm plot, built only on live data, and checks every number before the farmer hears it.

HOW IT WORKS
A farmer (or KVK worker) drops a pin on their field, picks the crop and sowing date, and asks a question by voice in Hindi, Marathi, Telugu, Tamil, Kannada, Bengali, Gujarati, Punjabi, Malayalam or English, e.g. "Should I irrigate this week?". An agent plans which tools it needs, calls them in parallel, and answers in the farmer's language with a source tag on every fact. The answer is also read aloud.

KEY FEATURES
• Plot-level satellite crop health: Sentinel-2 NDVI for an 80 m square around the pin, this season vs the same period last year, with cloud masking.
• Irrigation advice from an FAO-56 water balance: live soil moisture, crop water use from the crop's growth stage (14 crops), effective rain, soil texture from ISRIC SoilGrids. It answers "irrigate now / hold, rain is coming / due on 5 Nov, about 23 mm".
• A risk engine with official thresholds: heat stress at flowering, IMD cold wave and heavy rain, dry spells, spraying windows, disease-favourable weather, and rainfall vs the 10-year normal (IMD categories).
• Market advice: live Agmarknet prices from data.gov.in, nearest mandis by distance, and 7/30-day price trends that compare only markets reporting on both days. It also knows when the crop isn't ready to sell yet.
• Proactive monitoring: saved plots are re-checked every morning, and new risks become alerts (Telegram-ready) without the farmer asking.

WHY THE AGENT IS TRUSTWORTHY (not a chatbot wrapper)
• The LLM never does arithmetic. Deterministic engines (FAO-56, IMD rules) compute every number; the LLM plans and explains.
• A verifier checks every number in a draft answer against the tool outputs. If a number can't be traced, the agent is told exactly what failed and rewrites the answer; anything still unverifiable is removed. Pesticide doses are allowed only if they appear in retrieved agronomy documents (FAO-56, TNAU); otherwise the farmer is referred to the KVK or the Kisan Call Centre.
• Self-correction: malformed tool calls, failing data sources, rate limits and tool timeouts are fed back to the agent, which recovers instead of crashing.
• Robust against prompt injection: numbers typed by the user never count as evidence.
• Honest degradation: every data source has a fallback chain (e.g. Open-Meteo → MET Norway; ERA5 → NASA POWER; SoilGrids REST → SoilGrids map service; Nominatim → Photon). Cached data is labelled "stale" with its age, and missing data is shown as "unavailable". Nothing is ever made up.

TECHNICAL ARCHITECTURE
• Backend: Python, FastAPI, Server-Sent Events that stream the agent's reasoning, tool calls, timings, sources and fact-checks live to the UI. SQLite (WAL) for plots, alerts, a price-history snapshot store and a provenance-labelled cache.
• AI: Groq — gpt-oss-120b for tool calling, with a fallback chain (qwen3.8-27b, gpt-oss-20b) and API-key rotation; Whisper large-v3 for speech-to-text; Sarvam AI or gTTS for text-to-speech; a BM25 retrieval pipeline over FAO-56 and TNAU agronomy documents.
• Live data: Sentinel-2 L2A (Microsoft Planetary Computer / Copernicus Data Space / AWS Earth Search), Open-Meteo forecast and ERA5 archive, MET Norway, NASA POWER, ISRIC SoilGrids, data.gov.in Agmarknet (live + historical), OpenStreetMap.
• Frontend: plain HTML/JS with Leaflet satellite maps and Chart.js.
• Deployment: Docker + Caddy (HTTPS) on an Azure VM in Central India, because data.gov.in refuses non-Indian servers.

RELIABILITY, MEASURED
• 69 automated tests, including fault-injection tests that knock out providers, time out tools and inject garbage responses.
• A 12-scenario agent evaluation (5 languages, prompt injection, pesticide-dose requests, hallucination bait, missing sowing date, off-topic): 12/12 passed, 12/12 answers fully fact-checked, median answer time 3.9 s.
• A built-in Resilience Lab switches off a live provider on demand, so judges can watch the fallbacks work.

IMPACT & SCALE
One KVK worker can monitor hundreds of plots, getting alerts only where action is needed. Every data source is free and national-scale, so the cost per additional plot is close to zero. Next steps: WhatsApp and IVR voice calls for feature phones, field-boundary drawing, Sentinel-1 radar for cloudy monsoon weeks, pest models, and FPO and district dashboards.
```

---

## Links

| Field | Value |
|---|---|
| Live prototype link | https://20-207-206-244.sslip.io |
| Demo video link | _(to add after recording)_ |
| Public GitHub | https://github.com/KrisKeshav/khetdrishti |
| Presentation / pitch deck link | _(to add after the deck is made)_ |
