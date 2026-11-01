// Builds deck/KhetDrishti_pitch.pptx (7 slides). Every number is from the live app, docs/eval_results.md
// or the test suite. Run:  NODE_PATH=<dir with pptxgenjs, react-icons, react, react-dom, sharp> node deck/build_deck.js <screenshots dir>
const path = require("path");
const pptxgen = require("pptxgenjs");
const React = require("react");
const ReactDOMServer = require("react-dom/server");
const sharp = require("sharp");
const fa = require("react-icons/fa6");

const IMG_DIR = process.argv[2];
const OUT = path.join(__dirname, "KhetDrishti_pitch.pptx");

// Palette: deep field green dominates, saffron accent (India), water blue only for water topics.
const G = { dark: "12372A", mid: "1E6B48", leaf: "3FA06B", tint: "EAF4EE", ink: "1B2A22", muted: "5B6B62",
  saffron: "E8962E", water: "2A7FB8", white: "FFFFFF", line: "D5E3DA", red: "C0392B" };
const FONT = "Calibri";

async function icon(Comp, color = G.white, size = 256) {
  const svg = ReactDOMServer.renderToStaticMarkup(React.createElement(Comp, { color: "#" + color, size }));
  const png = await sharp(Buffer.from(svg)).resize(size, size).png().toBuffer();
  return "image/png;base64," + png.toString("base64");
}
async function jpg(file, width = 2400) {
  // High-res, high-quality: screenshots are captured at 2x so UI text stays sharp when the slide is enlarged.
  const buf = await sharp(path.join(IMG_DIR, file)).resize({ width, withoutEnlargement: true }).jpeg({ quality: 92, chromaSubsampling: "4:4:4" }).toBuffer();
  const meta = await sharp(buf).metadata();
  return { data: "image/jpeg;base64," + buf.toString("base64"), ratio: meta.height / meta.width };
}

(async () => {
  const pres = new pptxgen();
  pres.layout = "LAYOUT_WIDE"; // 13.33 x 7.5 in
  pres.title = "KhetDrishti — Forge '26 pitch";
  pres.author = "Team KhetDrishti";

  pres.defineSlideMaster({
    title: "CONTENT", background: { color: G.white },
    objects: [
      { placeholder: { options: { name: "title", type: "title", x: 0.6, y: 0.35, w: 12.1, h: 0.85, fontFace: FONT,
        fontSize: 32, bold: true, color: G.dark, valign: "middle", align: "left", margin: 0 }, text: "" } },
      { text: { text: "KhetDrishti · Forge '26 · Theme 2: Climate, Resource Infrastructure & Supply Resilience",
        options: { x: 0.6, y: 7.05, w: 9, h: 0.3, fontFace: FONT, fontSize: 10, color: G.muted, margin: 0 } } },
    ],
    slideNumber: { x: 12.35, y: 7.05, w: 0.4, h: 0.3, fontFace: FONT, fontSize: 10, color: G.muted },
  });
  pres.defineSlideMaster({ title: "DARK", background: { color: G.dark }, objects: [] });

  const I = {
    sat: await icon(fa.FaSatellite), drop: await icon(fa.FaDroplet), warn: await icon(fa.FaTriangleExclamation),
    rupee: await icon(fa.FaIndianRupeeSign), mic: await icon(fa.FaMicrophone), bell: await icon(fa.FaBell),
    map: await icon(fa.FaMapLocationDot), robot: await icon(fa.FaRobot), check: await icon(fa.FaCircleCheck),
    calc: await icon(fa.FaCalculator), ban: await icon(fa.FaBan), shield: await icon(fa.FaShieldHalved),
    hourglass: await icon(fa.FaHourglassHalf), question: await icon(fa.FaCircleQuestion),
    language: await icon(fa.FaLanguage), wheat: await icon(fa.FaWheatAwn), users: await icon(fa.FaUsers),
    bolt: await icon(fa.FaBolt), plug: await icon(fa.FaPlug), arrow: await icon(fa.FaArrowRight, G.saffron),
    rotate: await icon(fa.FaRotateLeft, G.saffron),
  };
  const shot = { report: await jpg("report_top.png"), charts: await jpg("charts.png"),
    ask: await jpg("ask.png"), outage: await jpg("outage.png"), cards: await jpg("cards.png") };

  const circleIcon = (slide, img, x, y, d, fill, name) => {
    slide.addShape(pres.shapes.OVAL, { x, y, w: d, h: d, fill: { color: fill }, line: { color: fill }, objectName: name + " circle" });
    const p = d * 0.25;
    slide.addImage({ data: img, x: x + p, y: y + p, w: d - 2 * p, h: d - 2 * p, objectName: name + " icon" });
  };
  const card = (slide, x, y, w, h, fill = G.tint, name = "card") =>
    slide.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, rectRadius: 0.12, fill: { color: fill },
      line: { color: fill }, objectName: name });
  const framedImage = (slide, img, x, y, w, name) => {
    const h = w * img.ratio;
    slide.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: x - 0.06, y: y - 0.06, w: w + 0.12, h: h + 0.12, rectRadius: 0.08,
      fill: { color: G.white }, line: { color: G.line, width: 1 },
      shadow: { type: "outer", color: "000000", opacity: 0.18, blur: 8, offset: 3, angle: 90 }, objectName: name + " frame" });
    slide.addImage({ data: img.data, x, y, w, h, objectName: name });
    return h;
  };
  const T = (slide, text, o) => slide.addText(text, { fontFace: FONT, isTextBox: true, margin: 0, ...o });

  // ------------------------------------------------------------------ 1. Title
  {
    const s = pres.addSlide({ masterName: "DARK" });
    T(s, "FORGE '26 · THEME 2 · CLIMATE & SUPPLY RESILIENCE",
      { x: 0.6, y: 0.55, w: 6.6, h: 0.3, fontSize: 11, bold: true, color: G.saffron, charSpacing: 2 });
    T(s, [{ text: "KhetDrishti", options: { fontSize: 54, bold: true, color: G.white, breakLine: true } },
      { text: "खेतदृष्टि · field vision", options: { fontSize: 22, color: "B8D8C5" } }],
      { x: 0.6, y: 1.1, w: 6.6, h: 1.7 });
    T(s, "A plot-level climate-risk AI agent for Indian farmers: live satellite, weather, soil and mandi data, "
      + "answered by voice in the farmer's language, with every number fact-checked.",
      { x: 0.6, y: 3.0, w: 6.2, h: 1.2, fontSize: 18, color: G.white, valign: "top" });
    const pills = [["10", "Indian languages"], ["14", "crops"], ["12/12", "agent eval passed"]];
    pills.forEach(([n, l], i) => {
      const x = 0.6 + i * 2.1;
      s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y: 4.45, w: 1.9, h: 1.0, rectRadius: 0.1, fill: { color: "1D4D3A" },
        line: { color: "2E6A52" }, objectName: "stat pill " + i });
      T(s, [{ text: n, options: { fontSize: 24, bold: true, color: G.saffron, breakLine: true } },
        { text: l, options: { fontSize: 11, color: "D4E7DB" } }], { x: x + 0.15, y: 4.52, w: 1.65, h: 0.86, valign: "middle" });
    });
    T(s, [{ text: "Live: ", options: { bold: true, color: G.saffron } },
      { text: "https://20-207-206-244.sslip.io", options: { color: G.white, hyperlink: { url: "https://20-207-206-244.sslip.io" } } },
      { text: "   ·   Code: ", options: { bold: true, color: G.saffron } },
      { text: "github.com/KrisKeshav/khetdrishti", options: { color: G.white, hyperlink: { url: "https://github.com/KrisKeshav/khetdrishti" } } }],
      { x: 0.6, y: 5.85, w: 6.8, h: 0.35, fontSize: 12 });
    T(s, "Team: Kris Keshav · Shivam Bansal (IIT Roorkee)", { x: 0.6, y: 6.35, w: 6.6, h: 0.35, fontSize: 13, color: "D4E7DB" });
    framedImage(s, shot.report, 7.45, 1.1, 5.35, "Live app screenshot: field report");
    s.addNotes("Introduce KhetDrishti: advice for ONE specific farm plot, built only on live public data, spoken in the farmer's language, and every number verified before the farmer hears it. The screenshot is the live app running in Central India.");
  }

  // ------------------------------------------------------------------ 2. Problem
  {
    const s = pres.addSlide({ masterName: "CONTENT" });
    s.addText("Weekly farm decisions, made on advice that ignores the field", { placeholder: "title" });
    const stats = [
      ["86%", "of Indian farm holdings are small or marginal", "Agriculture Census 2015-16", G.dark],
      ["−95%", "rain in 30 days vs the 10-year normal near Niphad, Nashik (IMD: large deficient)",
        "Measured live by KhetDrishti, Oct 2026", G.red],
      ["3", "high-stakes decisions every week: irrigate, spray, sell", "Wrong call = wasted water, lost crop, bad price", G.mid],
    ];
    stats.forEach(([n, l, src, c], i) => {
      const y = 1.45 + i * 1.75;
      card(s, 0.6, y, 5.6, 1.5, G.tint, "stat card " + i);
      T(s, n, { x: 0.85, y: y + 0.18, w: 1.9, h: 1.1, fontSize: 40, bold: true, color: c, valign: "middle" });
      T(s, [{ text: l, options: { fontSize: 15, color: G.ink, breakLine: true } },
        { text: src, options: { fontSize: 10, color: G.muted, italic: true } }], { x: 2.8, y: y + 0.15, w: 3.2, h: 1.2, valign: "middle" });
    });
    T(s, "Why generic AI chatbots fail here", { x: 6.85, y: 1.45, w: 5.8, h: 0.45, fontSize: 20, bold: true, color: G.dark });
    const fails = [
      [I.map, "They don't know the field", "District-level advice; no satellite view of this plot, its soil or its crop stage."],
      [I.question, "They make up numbers", "Confident mm, °C and ₹ figures with no source — dangerous for water and money."],
      [I.hourglass, "They wait to be asked", "A heat wave at flowering needs an alert, not a chat the farmer never starts."],
    ];
    fails.forEach(([img, h, d], i) => {
      const y = 2.15 + i * 1.3;
      circleIcon(s, img, 6.85, y, 0.75, i === 1 ? G.red : G.mid, "fail " + i);
      T(s, [{ text: h, options: { fontSize: 16, bold: true, color: G.ink, breakLine: true } },
        { text: d, options: { fontSize: 13, color: G.muted } }], { x: 7.8, y: y - 0.05, w: 4.9, h: 1.0, valign: "top" });
    });
    T(s, [{ text: "Who we serve: ", options: { bold: true, color: G.dark } },
      { text: "small and marginal farmers, and the KVK extension workers and FPOs who advise hundreds of plots each.", options: { color: G.ink } }],
      { x: 6.85, y: 6.1, w: 5.85, h: 0.6, fontSize: 13 });
    s.addNotes("86% small/marginal holdings (Agriculture Census 2015-16). The -95% is real: our app measured it for a Niphad plot in late October 2026 against the ERA5 10-year normal. Chatbots fail on three counts: no plot context, made-up numbers, purely reactive.");
  }

  // ------------------------------------------------------------------ 3. Solution & features
  {
    const s = pres.addSlide({ masterName: "CONTENT" });
    s.addText("One pin, live data, a verified answer in the farmer's language", { placeholder: "title" });
    const h = framedImage(s, shot.charts, 0.6, 1.45, 6.1, "Live satellite NDVI and 16-day forecast charts");
    const cw = Math.min(6.1, 3.15 / shot.cards.ratio);
    framedImage(s, shot.cards, 0.6 + (6.1 - cw) / 2, 1.45 + h + 0.3, cw, "Live irrigation, mandi and soil cards");
    const feats = [
      [I.sat, "Satellite crop health", "Sentinel-2 NDVI for this exact plot vs last year", G.mid],
      [I.drop, "Irrigation advice", "FAO-56 water balance: irrigate now / wait for rain / due date + mm", G.water],
      [I.warn, "Risk engine", "Heat at flowering, IMD cold wave & heavy rain, dry spell, spray window", G.saffron],
      [I.rupee, "Mandi prices", "Live Agmarknet, nearest markets, like-for-like 7/30-day trend", G.mid],
      [I.mic, "Voice, 10 languages", "Speak Hindi, Marathi, Telugu, Tamil…; hear the answer back", G.dark],
      [I.bell, "Proactive alerts", "Daily monitor re-checks saved plots and raises new risks", G.red],
    ];
    feats.forEach(([img, t, d, c], i) => {
      const col = i % 2, row = Math.floor(i / 2);
      const x = 7.05 + col * 2.9, y = 1.45 + row * 1.75;
      card(s, x, y, 2.7, 1.55, G.tint, "feature card " + i);
      circleIcon(s, img, x + 0.15, y + 0.15, 0.55, c, "feature " + i);
      T(s, t, { x: x + 0.8, y: y + 0.17, w: 1.8, h: 0.5, fontSize: 14, bold: true, color: G.ink, valign: "middle" });
      T(s, d, { x: x + 0.15, y: y + 0.78, w: 2.45, h: 0.72, fontSize: 11.5, color: G.muted, valign: "top" });
    });
    s.addNotes("Target users: farmers and KVK workers. Use cases: should I irrigate, is there a weather risk, should I sell and where, how healthy is my crop. All six features run on live public data; nothing is mocked.");
  }

  // ------------------------------------------------------------------ 4. Agent
  {
    const s = pres.addSlide({ masterName: "CONTENT" });
    s.addText("An agent that checks its own work before the farmer hears it", { placeholder: "title" });
    const steps = [["Voice question", "Whisper STT, any of 10 languages", I.mic, G.dark],
      ["Plan", "LLM picks tools (Groq gpt-oss-120b)", I.robot, G.mid],
      ["Act", "9 plot-scoped tools run in parallel", I.bolt, G.mid],
      ["Fact-check", "Every number must match a tool output", I.check, G.saffron],
      ["Translate & speak", "Numbers re-checked after translation", I.language, G.dark]];
    steps.forEach(([t, d, img, c], i) => {
      const x = 0.6 + i * 2.5;
      card(s, x, 1.4, 2.15, 1.5, G.tint, "agent step " + i);
      circleIcon(s, img, x + 0.79, 1.5, 0.56, c, "step " + i);
      T(s, t, { x: x + 0.1, y: 2.12, w: 1.95, h: 0.35, fontSize: 14, bold: true, color: G.ink, align: "center" });
      T(s, d, { x: x + 0.1, y: 2.46, w: 1.95, h: 0.42, fontSize: 11, color: G.muted, align: "center" });
      if (i < steps.length - 1) s.addImage({ data: I.arrow, x: x + 2.2, y: 2.02, w: 0.26, h: 0.26, objectName: "flow arrow " + i });
    });
    s.addImage({ data: I.rotate, x: 0.6, y: 3.04, w: 0.28, h: 0.28, objectName: "rewrite loop icon" });
    T(s, "Fails? The agent is told exactly which number failed and rewrites; anything still unverifiable is removed.",
      { x: 0.98, y: 3.02, w: 11.7, h: 0.32, fontSize: 12, italic: true, color: G.saffron });
    const guards = [[I.calc, "The LLM never does arithmetic", "FAO-56 and IMD engines compute every number; the LLM plans and explains."],
      [I.ban, "No invented pesticide doses", "A dose is allowed only if it appears in a retrieved FAO / TNAU document; otherwise refer to the KVK."],
      [I.shield, "Robust to prompt injection", "Numbers typed by the user never count as evidence."],
      [I.plug, "Self-correcting", "Bad tool calls, timeouts, failing sources and rate limits are fed back; the agent recovers."]];
    const aw = Math.min(5.9, 3.42 / shot.ask.ratio);   // screenshot as large as the space below the flow allows
    guards.forEach(([img, t, d], i) => {
      const y = 3.65 + i * 0.82;
      circleIcon(s, img, 0.6, y, 0.52, G.mid, "guard " + i);
      T(s, [{ text: t + "  ", options: { bold: true, color: G.ink } }, { text: d, options: { color: G.muted } }],
        { x: 1.3, y: y - 0.06, w: 12.75 - aw - 1.55, h: 0.7, fontSize: 12.5, valign: "middle" });
    });
    framedImage(s, shot.ask, 12.75 - aw, 3.55, aw, "Live Hindi answer with agent trace");
    s.addNotes("Explicit state machine: plan, act (parallel tools), draft, verify, self-correct, translate. The screenshot is a real Hindi answer: 4 numbers verified against live data, 2 tool calls, 2.8 s, with the live trace on the right.");
  }

  // ------------------------------------------------------------------ 5. Architecture
  {
    const s = pres.addSlide({ masterName: "CONTENT" });
    s.addText("Architecture: live data in, verified advice out, nothing made up", { placeholder: "title" });
    const lane = (x, w, title, items, fill, name) => {
      card(s, x, 1.45, w, 4.15, fill, name);
      T(s, title, { x: x + 0.2, y: 1.6, w: w - 0.4, h: 0.45, fontSize: 15, bold: true, color: fill === G.dark ? G.white : G.dark });
      T(s, items.map((t, i) => ({ text: t, options: { bullet: true, breakLine: i < items.length - 1 } })),
        { x: x + 0.2, y: 2.15, w: w - 0.35, h: 3.35, fontSize: 12, color: fill === G.dark ? "E3F0E8" : G.ink, valign: "top", paraSpaceAfter: 6 });
    };
    lane(0.6, 2.9, "1 · Live data sources", ["Sentinel-2 L2A (Planetary Computer / Copernicus / Earth Search)",
      "Open-Meteo forecast + soil moisture", "ERA5 10-yr rainfall", "ISRIC SoilGrids", "data.gov.in Agmarknet (live + history)",
      "OpenStreetMap geocoding"], G.tint, "lane sources");
    lane(3.7, 2.9, "2 · Fetch layer", ["Every call: timeout, retry, cache", "Provenance on every number: live / cache / stale + age",
      "Fallback chains: Open-Meteo → MET Norway, ERA5 → NASA POWER, SoilGrids REST → WCS, Nominatim → Photon",
      "Circuit breakers, time budgets"], G.tint, "lane fetch");
    lane(6.8, 2.9, "3 · Engines + agent", ["FAO-56 water balance, crop stage (14 crops)", "IMD-threshold risk rules",
      "BM25 RAG over FAO-56 + TNAU docs", "Groq agent + model fallback & key rotation", "Numeric verifier + dose guardrail"], G.tint, "lane engines");
    lane(9.9, 2.85, "4 · Delivery", ["FastAPI + Server-Sent Events (live trace)", "Leaflet + Chart.js web app",
      "Whisper STT · Sarvam / gTTS voice", "Daily monitor → alerts (Telegram-ready)", "Docker + Caddy HTTPS on Azure, Central India"], G.dark, "lane delivery");
    [3.45, 6.55, 9.65].forEach((x, i) => s.addImage({ data: I.arrow, x, y: 3.6, w: 0.22, h: 0.22, objectName: "lane arrow " + i }));
    T(s, [{ text: "Why India hosting: ", options: { bold: true, color: G.dark } },
      { text: "data.gov.in refuses non-Indian servers and shared cloud IPs get rate-limited, so we moved from a US PaaS to an Indian VM and added fallbacks for every provider.", options: { color: G.ink } }],
      { x: 0.6, y: 5.95, w: 12.1, h: 0.6, fontSize: 12.5 });
    s.addNotes("Four layers. Every external call goes through one fetch layer that adds timeouts, retries, labelled caching and provenance. Deterministic engines do the maths; the agent plans and explains; the verifier guards the output. Stack: Python, FastAPI, SQLite (WAL), Groq, rasterio, vanilla JS.");
  }

  // ------------------------------------------------------------------ 6. Proof
  {
    const s = pres.addSlide({ masterName: "CONTENT" });
    s.addText("Measured, not claimed: reliability you can check", { placeholder: "title" });
    const stats = [["12/12", "agent eval scenarios passed"], ["100%", "answers fully fact-checked (12/12)"],
      ["3.9 s", "median answer time"], ["69", "automated tests incl. fault injection"]];
    stats.forEach(([n, l], i) => {
      const x = 0.6 + i * 3.08;
      card(s, x, 1.45, 2.85, 1.35, G.tint, "proof stat " + i);
      T(s, n, { x: x + 0.2, y: 1.52, w: 2.5, h: 0.75, fontSize: 34, bold: true, color: i === 1 ? G.leaf : G.dark });
      T(s, l, { x: x + 0.2, y: 2.25, w: 2.5, h: 0.45, fontSize: 12, color: G.muted });
    });
    const scen = ["irrigation (hi)", "sell? (mr)", "crop health", "weather risk (te)", "Hinglish", "pesticide dose",
      "prompt injection", "hallucination bait", "no sowing date", "off-topic", "tiny input", "paddy (ta)"];
    const lat = [3.6, 9.9, 3.6, 10.2, 3.9, 4.2, 3.8, 0.9, 4.9, 0.6, 0.8, 10.5];
    s.addChart(pres.charts.BAR, [{ name: "Latency (s)", labels: scen, values: lat }], {
      x: 0.6, y: 3.0, w: 7.2, h: 3.85, barDir: "bar", chartColors: [G.mid], showValue: true, dataLabelPosition: "outEnd",
      dataLabelFontSize: 10, dataLabelColor: G.ink, dataLabelFormatCode: "0.0", dataLabelFontFace: FONT, catAxisLabelFontSize: 10, catAxisLabelFontFace: FONT,
      catAxisLabelColor: G.ink, valAxisHidden: true, valGridLine: { style: "none" }, catGridLine: { style: "none" },
      showTitle: true, title: "All 12 scenarios passed — answer time (s), live data + live LLM", titleFontSize: 12,
      titleColor: G.dark, titleFontFace: FONT, showLegend: false, catAxisOrientation: "maxMin", barGapWidthPct: 45,
      objectName: "Eval latency chart" });
    const h = framedImage(s, shot.outage, 8.1, 3.15, 4.6, "Resilience Lab: simulated Open-Meteo outage");
    T(s, [{ text: "Resilience Lab, live: ", options: { bold: true, color: G.dark } },
      { text: "switch off a provider and watch the fallback (here Open-Meteo → MET Norway + NASA POWER, with an amber banner).", options: { color: G.muted } }],
      { x: 8.1, y: 3.3 + h, w: 4.6, h: 0.75, fontSize: 11.5 });
    s.addNotes("Every figure here is reproducible from the repo: scripts/eval_agent.py (12 scenarios across 5 languages, plus injection, dose and hallucination traps), pytest (69 tests, including fault injection) and the in-app Resilience Lab. Real production bugs we found and fixed: SQLite locking under load, Nominatim rate-limiting cloud IPs, and a missing native library in the container.");
  }

  // ------------------------------------------------------------------ 7. Impact, roadmap, demo
  {
    const s = pres.addSlide({ masterName: "DARK" });
    T(s, "From one field to every KVK", { x: 0.6, y: 0.4, w: 12.1, h: 0.8, fontSize: 32, bold: true, color: G.white });
    const impact = [[I.users, "One KVK worker, hundreds of plots", "Alerts only where action is needed"],
      [I.wheat, "Near-zero cost per extra plot", "Every data source is free and national-scale"],
      [I.language, "Built for India", "14 crops, 10 languages, IMD + FAO-56 science"]];
    impact.forEach(([img, t, d], i) => {
      const y = 1.5 + i * 1.15;
      circleIcon(s, img, 0.6, y, 0.7, G.mid, "impact " + i);
      T(s, [{ text: t, options: { fontSize: 16, bold: true, color: G.white, breakLine: true } },
        { text: d, options: { fontSize: 12.5, color: "C9DFD1" } }], { x: 1.5, y: y - 0.02, w: 4.4, h: 0.8, valign: "middle" });
    });
    const road = [["NOW · live", ["Web + voice agent", "14 crops, 10 languages", "Daily monitor + alerts"], G.leaf],
      ["NEXT", ["WhatsApp + IVR calls for feature phones", "Draw exact field boundaries", "Sentinel-1 radar for cloudy monsoon weeks"], G.saffron],
      ["LATER", ["Pest & disease models", "FPO / district dashboards", "Crop-insurance (PMFBY) triggers"], "8FB9A4"]];
    road.forEach(([t, items, c], i) => {
      const x = 6.35 + i * 2.15;
      s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y: 1.5, w: 1.95, h: 3.0, rectRadius: 0.1, fill: { color: "1D4D3A" },
        line: { color: c, width: 1.5 }, objectName: "roadmap " + i });
      T(s, t, { x: x + 0.15, y: 1.62, w: 1.65, h: 0.4, fontSize: 13, bold: true, color: c });
      T(s, items.map((it, j) => ({ text: it, options: { bullet: true, breakLine: j < items.length - 1 } })),
        { x: x + 0.12, y: 2.05, w: 1.72, h: 2.6, fontSize: 11.5, color: G.white, valign: "top", paraSpaceAfter: 5 });
    });
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 0.6, y: 5.15, w: 12.1, h: 1.55, rectRadius: 0.12, fill: { color: G.white },
      line: { color: G.white }, objectName: "demo summary card" });
    T(s, "Demo summary", { x: 0.85, y: 5.27, w: 4, h: 0.4, fontSize: 15, bold: true, color: G.dark });
    T(s, "Pin a real field → live satellite, weather, soil and mandi report → Hindi voice question → agent plans, calls tools, fact-checks, "
      + "answers aloud → guardrails refuse invented doses and injected prices → Resilience Lab knocks out a provider live → daily monitor raises alerts.",
      { x: 0.85, y: 5.65, w: 11.6, h: 0.65, fontSize: 12.5, color: G.ink });
    T(s, [{ text: "Live: ", options: { bold: true, color: G.mid } },
      { text: "https://20-207-206-244.sslip.io", options: { color: G.water, hyperlink: { url: "https://20-207-206-244.sslip.io" } } },
      { text: "    Code: ", options: { bold: true, color: G.mid } },
      { text: "github.com/KrisKeshav/khetdrishti", options: { color: G.water, hyperlink: { url: "https://github.com/KrisKeshav/khetdrishti" } } }],
      { x: 0.85, y: 6.32, w: 11.6, h: 0.3, fontSize: 12 });
    s.addNotes("Scale through KVKs and FPOs; free national data means near-zero marginal cost. Roadmap: WhatsApp and IVR for feature phones next. Close with the demo summary and both links.");
  }

  await pres.writeFile({ fileName: OUT });
  console.log("wrote", OUT);
})();
