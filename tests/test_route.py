"""Router tests against a mocked basal-serve (httpx.MockTransport)."""

import asyncio

import httpx
import pytest

from coi_formularze.config import Settings
from coi_formularze.extract.model import Document, Page, Source
from coi_formularze.route import (
    Route,
    Tier,
    build_state,
    escalate,
    features_of,
    route_document,
)
from coi_formularze.route.router import TRUNCATION_MARK, parse_answer


def _doc(text="Imię: ........\nNazwisko: ........\n☐ tak ☐ nie", pages=1, ocr=False):
    src = Source.OCR if ocr else Source.NATIVE
    return Document(kind="pdf", pages=[Page(number=i + 1, source=src, markdown=text)
                                       for i in range(pages)])


def _answer(choice="simple", conf=0.9):
    return {"model": "basal-omni-pico", "answers": {"tier": {
        "type": "choice", "choice": choice, "confidence": conf,
        "probabilities": {"simple": conf, "medium": 1 - conf, "hard": 0.0}}},
        "usage": {"latency_ms": 234}}


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _settings(**kw):
    return Settings(_env_file=None, router_enabled=True, router_url="http://basal", **kw)


def _route(doc, handler, **kw):
    async def run():
        async with _client(handler) as c:
            return await route_document(doc, _settings(**kw), client=c)
    return asyncio.run(run())


def test_confident_answer_is_used():
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        seen["body"] = req.read().decode()
        return httpx.Response(200, json=_answer("medium", 0.9))

    r = _route(_doc(), handler)
    assert r == Route(tier=Tier.MEDIUM, confidence=0.9,
                      probabilities={"simple": 0.9, "medium": pytest.approx(0.1), "hard": 0.0},
                      source="basal")
    assert seen["url"] == "http://basal/v1/systemone"
    assert "szacowana liczba pól" in seen["body"]


def test_low_confidence_bumps_one_tier():
    r = _route(_doc(), lambda req: httpx.Response(200, json=_answer("simple", 0.5)))
    assert (r.tier, r.escalated, r.source) == (Tier.MEDIUM, True, "basal")


def test_threshold_is_configurable():
    r = _route(_doc(), lambda req: httpx.Response(200, json=_answer("simple", 0.5)),
               router_threshold=0.4)
    assert (r.tier, r.escalated) == (Tier.SIMPLE, False)


def test_hard_stays_hard_on_bump():
    r = _route(_doc(), lambda req: httpx.Response(200, json=_answer("hard", 0.1)))
    assert (r.tier, r.escalated) == (Tier.HARD, False)


def test_fallback_when_starting():
    r = _route(_doc(), lambda req: httpx.Response(503, json={"status": "starting"}))
    assert r.source == "heuristic" and r.tier is Tier.SIMPLE
    assert "starting" in r.reason


def test_fallback_when_down():
    def handler(req):
        raise httpx.ConnectError("refused")

    r = _route(_doc(pages=8), handler)
    assert (r.source, r.tier) == ("heuristic", Tier.HARD)


@pytest.mark.parametrize("body", [{"answers": {}}, {"answers": {"tier": {"choice": "x",
                                                                          "confidence": 1}}}])
def test_fallback_on_garbage(body):
    r = _route(_doc(), lambda req: httpx.Response(200, json=body))
    assert r.source == "heuristic"


def test_disabled_never_calls():
    def handler(req):
        raise AssertionError("called")

    async def run():
        async with _client(handler) as c:
            return await route_document(_doc(), Settings(_env_file=None), client=c)

    assert asyncio.run(run()).reason == "router disabled"


def test_no_document_routes_hard():
    assert asyncio.run(route_document(None, _settings())).tier is Tier.HARD


def test_truncation():
    doc = _doc("x" * 50_000)
    state = build_state(features_of(doc), doc.markdown, 1000)
    assert len(state) == 1000 and state.endswith(TRUNCATION_MARK)
    assert state.startswith("Format: pdf")
    short = build_state(features_of(_doc()), "abc", 1000)
    assert short.endswith("abc")


def test_features():
    text = "| a | b |\n|---|---|\n| Imię |  |\n\nData: ....... ☐"
    f = features_of(_doc(text, pages=2, ocr=True))
    assert (f.pages, f.tables, f.scanned) == (2, 2, True)
    assert f.est_fields == 6  # per page: blank, checkbox, empty cell


def test_parse_answer_starting_raises():
    from coi_formularze.route import BasalUnavailable
    with pytest.raises(BasalUnavailable):
        parse_answer({"status": "starting"})


def test_escalate():
    r = Route(tier=Tier.SIMPLE, confidence=0.9, source="basal")
    e = escalate(r)
    assert (e.tier, e.escalated, e.reason) == (Tier.MEDIUM, True, "validation failed")
    assert escalate(escalate(e)).tier is Tier.HARD
