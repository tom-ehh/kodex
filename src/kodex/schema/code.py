"""Pieces filled by code: the same for every form, never asked of the LLM."""

from enum import StrEnum

from pydantic import Field

from kodex.schema.common import Model, NonEmpty


class Layout(Model):
    """Grid placement of a question: CSS grid-area: y+1 / x+1 / span rows / span cols."""

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    rows: int = Field(ge=1)
    cols: int = Field(ge=1)


class Placement(Model):
    """Identity of a field on the page."""

    id: NonEmpty
    questionOrder: float | None = None


class TabType(StrEnum):
    MAIN = "MAIN"
    FINISH = "FINISH"


class TabHeader(Model):
    id: NonEmpty
    title: str
    type: TabType


class Link(Model):
    """Navigation from tab `source` to tab `target` (rule 7)."""

    id: NonEmpty
    source: NonEmpty
    target: NonEmpty


class Flow(Model):
    links: list[Link]
