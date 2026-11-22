"""Morpheus HTTP API: skills listing and the human review queue (design.md §5)."""

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from kodex.api.deps import MorpheusDep, SettingsDep
from kodex.morpheus import review
from kodex.morpheus.models import Layer, Skill, SkillStatus
from kodex.morpheus.wake import Wake


class ReviewDecision(BaseModel):
    decision: Literal["approve", "reject"] = Field(description="Approve or reject the item.")


class ReviewResult(BaseModel):
    id: str
    status: str
    result: dict


def _need(store):
    if store is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Morpheus is disabled.")
    return store


def build_router(dependencies=None, responses=None) -> APIRouter:
    r = APIRouter(prefix="/api/v1/morpheus", tags=["morpheus"], dependencies=dependencies or [],
                  responses=responses or {})
    disabled = {503: {"description": "Morpheus is disabled."}}

    @r.get("/skills", summary="List Morpheus skills", responses=disabled)
    def list_skills(store: MorpheusDep,
                    layer: Annotated[Layer | None, Query()] = None,
                    status_: Annotated[SkillStatus | None, Query(alias="status")] = None) -> list[Skill]:
        """Latest version of every skill (seed, episodic, general), optionally filtered."""
        return _need(store).list_skills(layer, status_)

    @r.get("/review", summary="List the review queue", responses=disabled)
    def list_review(store: MorpheusDep, all: bool = False) -> list[review.ReviewItem]:
        """Pending items (proposed skills with their diff and replay result, and episodes
        that need a human). `all=true` includes decided items."""
        return review.items(_need(store), None if all else "pending")

    @r.post("/review/{item_id}", summary="Approve or reject a review item",
            responses=disabled | {404: {"description": "No pending item with this id."}})
    def decide(item_id: str, body: ReviewDecision, request: Request, store: MorpheusDep,
               settings: SettingsDep) -> ReviewResult:
        """Approve: a proposed skill becomes active (new skillset version); a needs-human
        episode is re-queued for wake with the human's approval (the light gate still
        runs). Reject: the item is closed and its episode ignored."""
        wake = getattr(request.app.state, "wake", None)
        store = _need(store)
        if wake is None:
            wake = Wake(store, settings)
        try:
            res = wake.approve(item_id) if body.decision == "approve" else wake.reject(item_id)
        except KeyError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No pending item with this id.") from None
        return ReviewResult(id=item_id, status="approved" if body.decision == "approve"
                            else "rejected", result=res)

    return r
