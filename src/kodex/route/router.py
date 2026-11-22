"""Route a document to an extractor tier via basal-serve, with a heuristic fallback."""

import logging

import httpx

from kodex.extract.model import Document
from kodex.route.features import Features, features_of
from kodex.route.model import Route, Tier

log = logging.getLogger(__name__)

QUESTION = {
    "type": "choice",
    "instructions": "Jak złożony jest ten formularz do ekstrakcji pól?",
    "criteria": {
        "simple": "Krótki formularz (1-2 strony), kilkanaście prostych pól tekstowych, "
        "czytelny tekst, brak lub proste tabele.",
        "medium": "Kilka stron lub kilkadziesiąt pól, sekcje, pola wyboru, proste tabele "
        "do wypełnienia.",
        "hard": "Długi lub skanowany formularz, złożone tabele, warunkowe sekcje, wiele "
        "pól powtarzalnych lub nieczytelny tekst.",
    },
}

TRUNCATION_MARK = "\n[…tekst skrócony…]"


class BasalUnavailable(Exception):
    """basal-serve is down, still loading, or answered something unusable."""


def build_state(features: Features, text: str, max_chars: int) -> str:
    """Features header + extracted text, cut so the prompt stays under the model's limit."""
    header = features.summary() + "\n\n"
    budget = max(max_chars - len(header), 0)
    if len(text) > budget:
        text = text[: max(budget - len(TRUNCATION_MARK), 0)] + TRUNCATION_MARK
    return header + text


def heuristic_route(features: Features, reason: str) -> Route:
    """Deterministic tier from features alone."""
    f = features
    if f.pages > 6 or f.est_fields > 60 or (f.scanned and f.est_fields > 20) or f.tables > 4:
        tier = Tier.HARD
    elif f.pages > 2 or f.est_fields > 20 or f.tables > 1 or f.scanned:
        tier = Tier.MEDIUM
    else:
        tier = Tier.SIMPLE
    return Route(tier=tier, confidence=1.0, source="heuristic", reason=reason)


def parse_answer(body: object) -> Route:
    """Turn a /v1/systemone response into a Route; raise BasalUnavailable if unusable."""
    if not isinstance(body, dict):
        raise BasalUnavailable("response is not an object")
    if body.get("status") == "starting":
        raise BasalUnavailable("basal is starting")
    try:
        answer = body["answers"]["tier"]
        tier = Tier(answer["choice"])
        confidence = float(answer["confidence"])
        probs = {str(k): float(v) for k, v in (answer.get("probabilities") or {}).items()}
    except (KeyError, TypeError, ValueError) as exc:
        raise BasalUnavailable(f"unexpected response: {exc!r}") from exc
    return Route(tier=tier, confidence=confidence, probabilities=probs, source="basal")


async def ask_basal(state: str, url: str, timeout: float, client: httpx.AsyncClient | None = None) -> Route:
    payload = {"state": state, "questions": {"tier": QUESTION}}
    endpoint = url.rstrip("/") + "/v1/systemone"
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=timeout) as c:
                resp = await c.post(endpoint, json=payload)
        else:
            resp = await client.post(endpoint, json=payload, timeout=timeout)
        body = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise BasalUnavailable(f"{type(exc).__name__}: {exc}") from exc
    if resp.status_code == 503:
        raise BasalUnavailable("basal is starting or overloaded")
    if resp.status_code != 200:
        raise BasalUnavailable(f"HTTP {resp.status_code}")
    return parse_answer(body)


async def route_document(
    doc: Document | None, settings, client: httpx.AsyncClient | None = None
) -> Route:
    """Pick a tier for `doc` (None when extraction failed). Never raises."""
    if doc is None:
        return Route(tier=Tier.HARD, confidence=1.0, source="heuristic",
                     reason="text extraction failed")
    features = features_of(doc)
    if not settings.router_enabled:
        return heuristic_route(features, "router disabled")
    state = build_state(features, doc.markdown, settings.router_max_state_chars)
    try:
        route = await ask_basal(state, settings.router_url, settings.router_timeout_s, client)
    except BasalUnavailable as exc:
        log.info("router fallback: %s", exc)
        return heuristic_route(features, f"basal unavailable: {exc}")
    except Exception as exc:  # the API must never fail because of the router
        log.exception("router error")
        return heuristic_route(features, f"router error: {type(exc).__name__}")
    if route.confidence < settings.router_threshold:
        return route.bumped(
            f"confidence {route.confidence:.2f} < threshold {settings.router_threshold}"
        )
    return route


def escalate(route: Route, reason: str = "validation failed") -> Route:
    """Move a route one tier up, e.g. when the extractor's output failed validation."""
    return route.bumped(reason)


def tier_models(settings) -> dict[Tier, str]:
    """Configured extractor model per tier."""
    return {
        Tier.SIMPLE: settings.tier_simple_model,
        Tier.MEDIUM: settings.tier_medium_model,
        Tier.HARD: settings.tier_hard_model,
    }
