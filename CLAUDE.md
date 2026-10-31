# CLAUDE.md — KhetDrishti (Forge '26 Gen AI Buildathon)

Read this file before every change. It is the source of truth for what we are allowed to build and how.
If a request conflicts with a rule below, say so and stop — do not quietly work around it.

---

## 1. Hackathon facts (do not forget)

- Event: Forge '26 Gen AI Buildathon (NxtWave). Teams of 2–3. Build window: 48 h from 30 Oct 2026, 19:30 IST.
- Our track: **Theme 2 — Climate, Resource Infrastructure & Global Supply Resilience**.
- Our problem framing: fusion of *Multilingual Agritech & Resource Advisory Assistant* + *Predictive Climate & Micro-Location Yield Engine*.
- Finale: top 10 teams present live; judges pick 2 winners, audience picks 1 favourite.

### Scoring rubric (100)
| Pillar | Pts | What judges look for |
|---|---|---|
| Technical Architecture & Engineering Depth | 20 | Clean modular code, efficient API use, low latency, **not a thin wrapper** (custom RAG, geospatial, state graphs) |
| AI Reasoning & Agentic Execution | 25 | Multi-step tool calls, planning, **error handling + self-correction**, accuracy, **guardrails**, **source attribution**, low hallucination |
| Problem Relevance, Impact & USP | 25 | Real value for real people, clear differentiation from off-the-shelf chatbots, scalability |
| Live Demonstration & Robustness | 30 | Runs live end-to-end **without crashing**; graceful handling of bad input, missing data, network failure |

### HARD RULES — violating any = disqualification
1. **NO DUMMY DATA. Zero tolerance.** No hardcoded JSON results, no static mock datasets, no fake placeholder numbers, no "sample" responses shown as real. Every number on screen must come from a live API, a live database populated from live APIs, or an official open dataset — and must show its source + fetch time.
   - Allowed: agronomic *reference parameters* with a cited source (FAO-56 crop coefficients, IMD category thresholds) in `data/crop_profiles.yaml`. These are science constants, not data. Every value there MUST carry a `source`.
   - Allowed: caching real API responses (always labelled `live` / `cache` / `stale` with age).
   - NOT allowed: inventing a fallback number when an API fails. If data is unavailable, say "unavailable" in the UI and in the agent answer.
   - Tests may use fixtures **only inside `tests/`**; never import test fixtures from `app/`.
2. **No pre-recorded demo disguised as live.** The demo video must show the real app running.
3. **Core logic built during the hackathon window.** Libraries/boilerplate OK; agent, tools, risk engine, pipelines are ours.
4. **Every team member must be able to explain** the architecture, data flow and AI logic. Keep code readable; prefer plain Python over clever abstractions. Comment the *why*.
5. No plagiarism, no code from other teams, follow the code of conduct.

### Submission checklist (all required)
- [ ] Problem statement (target issue + audience)
- [ ] Solution description (overview + technical architecture: stack, models, datasets, design)
- [ ] **Live hosted URL** (HF Spaces / Render) — must work during judging
- [ ] **2–3 min demo video** — end-to-end, live, showing real-time AI reasoning, tool usage, agentic execution
- [ ] **Public GitHub repo** — clean README with setup, **architecture diagram**, all source + config (no secrets!)
- [ ] **5–7 slide deck**: Title & team · Problem · Solution & features · Architecture & innovation · Roadmap & demo summary

---

## 2. Product in one paragraph

KhetDrishti ("field vision") is a voice-first, multilingual climate-risk agent for **one specific farm plot**.
The farmer drops a pin (or registers a plot); the system pulls Sentinel-2 satellite NDVI for *that* plot, a 16-day
weather + soil-moisture forecast, a rainfall anomaly vs the last 10 years, soil texture, and live mandi prices.
A deterministic risk engine (FAO-56 water balance, IMD thresholds, crop-stage-aware heat/cold/rain rules) produces
alerts. An LLM agent (Groq) answers questions by calling these tools, then a **verifier** checks every number in the
answer against tool outputs before it is translated and spoken in the farmer's language. A daily monitor scans all
registered plots and pushes alerts (Telegram) without being asked.

**USP vs other teams:** plot-level satellite evidence · proactive alerts · every number cited & verified ·
LLM never does arithmetic (tools do) · graceful degradation with labelled fallbacks.

---

## 3. Architecture & where things live

```
app/
  config.py            settings from .env (pydantic-settings)
  db.py                SQLAlchemy Core tables: cache, plots, alerts, mandi_snapshots, agent_runs
  fetch.py             fetch_json(): live → retry → stale-cache fallback, returns data + Provenance
  data_sources/        one module per external source (geocode, weather, climate, satellite, soil, mandi)
  knowledge/crops.py   crop profiles (FAO-56 Kc, stages, thresholds) + crop-stage maths
  knowledge/rag.py     BM25 retrieval over ingested agronomy documents (with citations)
  engine/agro.py       pure functions: ET0 (Hargreaves), effective rain, soil texture, water balance
  engine/risk.py       deterministic risk rules → list of Risk with evidence
  engine/report.py     builds the full field report (parallel fetch of all sources)
  agent/llm.py         Groq client: model fallback chain + API-key rotation on 429
  agent/tools.py       tool schemas + implementations (scoped to the selected plot)
  agent/verifier.py    numeric grounding + pesticide-dosage guardrail
  agent/orchestrator.py  plan → act → observe → verify → (self-correct) → translate; yields SSE events
  voice/stt.py, tts.py Groq Whisper STT; Sarvam → gTTS TTS chain (browser speech as last fallback)
  monitor.py           daily scan of registered plots → alerts → Telegram
  main.py              FastAPI routes + serves static/ frontend
static/                vanilla HTML/JS/CSS (Leaflet + Chart.js from CDN) — no build step
scripts/               smoke_test.py (checks every live API), ingest_knowledge.py (builds RAG index)
tests/                 pytest unit tests for pure logic
```

Data sources (all real, all free):
| Source | Used for | Auth |
|---|---|---|
| Open-Meteo Forecast API | 16-day weather, ET0, soil moisture | none |
| Open-Meteo Archive API | 10-yr rainfall baseline | none |
| MET Norway Locationforecast | weather fallback (+ Hargreaves ET0) | none (User-Agent) |
| NASA POWER | historical rainfall fallback | none |
| Copernicus Data Space (Sentinel Hub Statistical API) | Sentinel-2 NDVI (used first when credentials set) | free OAuth client |
| Microsoft Planetary Computer (STAC + data API zonal stats) | Sentinel-2 NDVI (default, server-side, ~7 s cold) | none |
| Element84 Earth Search STAC + AWS COGs | Sentinel-2 NDVI (last resort; too slow from India, ~26 KB/s) | none |
| ISRIC SoilGrids | soil texture, pH, organic carbon | none |
| data.gov.in (Agmarknet) | live + historical mandi prices | free API key |
| OpenStreetMap Nominatim | geocoding, India check | none (User-Agent, 1 req/s) |
| Groq | LLM (tool calling) + Whisper STT | free API key |

---

## 4. Coding rules

- Python 3.12, FastAPI, sync code + `ThreadPoolExecutor` for parallel fetches (easy to explain).
- **Every external call goes through `app/fetch.py`** so it gets timeout, retry, caching and provenance.
- Every tool/data function returns `{"data": ..., "sources": [Provenance...]}` or raises `DataUnavailable`.
  Never return a made-up default on failure.
- The LLM must not compute numbers. If an answer needs a derived number, add it to a tool output.
- Keep tool outputs compact (Groq free tier has low tokens-per-minute limits).
- Don't put secrets in code. `.env` is git-ignored; document new variables in `.env.example`.
- When adding a crop parameter, add its `source` in `crop_profiles.yaml`.
- Run `pytest` and `python scripts/smoke_test.py` before every commit that touches data/agent code.
- Frontend: no frameworks, no build step. Keep `static/app.js` organised by section comments.

## 5. Demo-safety rules

- Before the live demo / any deploy: `python scripts/preflight.py --full` must print **GO**
  (pytest incl. fault-injection tests → live smoke test → agent eval battery → browser check).
- Save the demo plots ("Save & monitor"): the server re-warms the cache for saved plots at startup and every 45 min.
- Resilience lab (Monitoring tab / `POST /api/resilience/outage`) simulates provider outages for ≤30 min; use it
  in the demo to show fallbacks. It must never be left on — it auto-expires and shows an amber banner.
- Verifier evidence = tool outputs only. Never add the user's question to evidence (prompt-injection hole).
- All DB writes go through `db.write()` (WAL + process write lock) — parallel writes otherwise hit "database is locked".
- Any new external call: give it a timeout, go through `fetch_json`, and make sure the report/tool time budgets
  (`REPORT_BUDGET_S`, `TOOL_TIMEOUT_S`) still bound it.
- Sentinel-2 L2A digital numbers from processing baseline 04.00 onward carry a +1000 offset on Planetary Computer
  (NDVI = (B08-B04)/(B08+B04-2000)); Earth Search v1 has it removed. Getting this wrong gives NDVI > 1.
- Never demo a code path that has not passed the smoke test that day.
- Edge cases that must work: pin in ocean / outside India · cloudy satellite period · API timeout ·
  unknown crop · empty mandi day · noisy/mixed-language audio · off-topic question · Groq rate limit.
