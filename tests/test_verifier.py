from app.agent.verifier import evidence_numbers, extract_numbers, strip_unsupported, verify

TOOL_DATA = [{"tmax": 36.4, "rain_mm": 12.3, "date": "2026-11-03", "modal": 6811, "market": "Lasalgaon"}]


def test_extracts_indian_formatted_numbers_and_ignores_tags():
    assert 6811.0 in extract_numbers("Rs 6,811 per quintal [Agmarknet]")
    assert extract_numbers("[ERA5]") == []


def test_grounded_answer_passes_with_rounding():
    ev = evidence_numbers(TOOL_DATA)
    r = verify("Max 36 °C on 3 Nov, rain 12.3 mm, Lasalgaon pays Rs 6,800.", ev, [])
    assert r["passed"], r


def test_invented_number_fails():
    ev = evidence_numbers(TOOL_DATA)
    r = verify("Rain will be 45 mm and price Rs 7,500.", ev, [])
    assert not r["passed"]
    assert 45 in r["unsupported_numbers"] and 7500 in r["unsupported_numbers"]


def test_small_counts_are_allowed():
    r = verify("Irrigate within 3 days, check again in 7 days.", set(), [])
    assert r["passed"]


def test_dose_needs_document_support():
    ev = evidence_numbers(TOOL_DATA) | {2.0}
    bad = verify("Spray 2 ml per litre of water.", ev, [])
    assert bad["dose_violations"]
    ok = verify("Spray 2 ml per litre of water.", ev, ["Apply 2 ml per litre at flowering."])
    assert not ok["dose_violations"]


def test_strip_removes_only_bad_sentences():
    ev = evidence_numbers(TOOL_DATA)
    text = "Max will be 36 °C. Rain will be 45 mm."
    out = strip_unsupported(text, verify(text, ev, []))
    assert "36" in out and "45" not in out
