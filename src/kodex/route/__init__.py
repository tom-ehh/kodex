"""System One complexity router: pick an extractor tier for a form before extraction.

The decision comes from a small typed-decision model (basal-omni-pico) served by
`basal-serve`; when it is disabled, down or still loading, a deterministic feature-based
fallback decides instead. Routing never raises: the API must not fail because of it.
"""

from kodex.route.features import Features, features_of
from kodex.route.model import Route, Tier
from kodex.route.router import (
    BasalUnavailable,
    build_state,
    escalate,
    heuristic_route,
    route_document,
    tier_models,
)

__all__ = [
    "BasalUnavailable",
    "Features",
    "Route",
    "Tier",
    "build_state",
    "escalate",
    "features_of",
    "heuristic_route",
    "route_document",
    "tier_models",
]
