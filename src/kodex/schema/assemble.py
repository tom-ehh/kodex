"""FormDraft (all model output for one form) -> Form Definition dict.

Deterministic. Pages and sections come from the outline as the model drafted them;
code adds tech names, ids, grid positions, question order, flow links, every JSONata
query and a FINISH tab. No standard sections or templates are added.

`DraftError` means the drafts contradict each other (a retry with the message can fix
it). The result is checked with `validate_form_definition`; issues there are
assembler bugs, not model errors.
"""

import itertools
import uuid
from collections.abc import Callable, Iterable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from kodex.schema import initial_data
from kodex.schema.drafts import (
    FieldDraft,
    FormFacts,
    Outline,
    OutlineSection,
    SectionDraft,
)
from kodex.schema.kinds import (
    TERYT_KINDS,
    TERYT_LEVELS,
    Context,
    build,
    make_field,
    teryt_question,
)
from kodex.schema.naming import slug, unique
from kodex.schema.states import (
    Location,
    StateDraft,
    catalogue,
    leaves,
    state_problems,
    to_jsonata,
)

FINISH_TITLE = "Podsumowanie"
FINISH_TEXT = "Sprawdź dane przed wysłaniem wniosku."
GRID_COLS = 3
PostBuild = Callable[[FieldDraft, list[dict], str], None]  # (field, questions, section title)


class DraftError(ValueError):
    """The drafts contradict each other; the message says how."""


class FormDraft(BaseModel):
    """All model output for one form. Built by the pipeline, or by hand in tests."""

    model_config = ConfigDict(extra="forbid")

    facts: FormFacts
    outline: Outline
    sections: list[SectionDraft]
    states: list[StateDraft] = []

    @model_validator(mode="after")
    def _consistent(self):
        planned = {slug(s.title) for s in self.outline.sections()}
        drafted = {slug(s.title) for s in self.sections}
        missing, extra = sorted(planned - drafted), sorted(drafted - planned)
        if missing or extra:
            raise ValueError(f"sections do not match the outline: missing {missing}, extra {extra}")
        if any(p.title == FINISH_TITLE for p in self.outline.pages):
            raise ValueError(f"page title {FINISH_TITLE!r} is reserved for the summary page")
        problems = state_problems(self.states, catalogue(self.outline, self.sections))
        if problems:
            raise ValueError("; ".join(problems))
        return self


class DictionaryProposal(BaseModel):
    """Extension II: a new dictionary suggested for a reusable list of options."""

    name: str
    description: str
    entries: list[str]
    used_by: list[str] = Field(description="techNames of the fields that would use it")


class Assembled(BaseModel):
    form: dict
    dictionary_proposals: list[DictionaryProposal] = []


def assemble(draft: FormDraft, post_build: PostBuild | None = None) -> Assembled:
    """`post_build(field_draft, questions, section_title)` may adjust each built field in place (Morpheus rules)."""
    builder = _Builder(set(), {slug(s.title): s for s in draft.sections}, post_build)
    builder.addressee = draft.facts.addressee
    pages = [(p.title, builder.sections(p.title, p.sections)) for p in draft.outline.pages]
    finish = make_field("SECTION", unique("podglad", builder.taken), "Podgląd", repeatingSectionGrid=[
        {"field": make_field("INFO_SECTION", unique("podglad_info", builder.taken), description=FINISH_TEXT)}
    ])
    pages.append((FINISH_TITLE, [{"field": finish}]))
    builder.apply_states(draft.states)

    tabs = [
        {"id": _id(), "title": title, "type": "FINISH" if i == len(pages) - 1 else "MAIN",
         "questions": _layout(questions)}
        for i, (title, questions) in enumerate(pages)
    ]
    for tab in tabs:
        for q in tab["questions"]:
            for f in _fields(q):
                f["id"] = _id()
    links = [{"id": _id(), "source": a["id"], "target": b["id"]} for a, b in itertools.pairwise(tabs)]
    form = {"title": draft.facts.title, "tabs": tabs, "flow": {"links": links}}
    return Assembled(form=form, dictionary_proposals=builder.proposals())


class _Builder:
    """Builds drafted sections and remembers where every catalogue key landed."""

    def __init__(self, taken: set[str], drafted: dict[str, SectionDraft],
                 post_build: PostBuild | None = None):
        self.taken = taken
        self.post_build = post_build
        self.drafted = drafted
        self.where: dict[str, Location] = {}
        self.question: dict[str, dict] = {}  # key -> the question whose field it is
        self._proposals: dict[str, DictionaryProposal] = {}
        self.addressee: str | None = None

    def sections(self, tab: str, plans: Iterable[OutlineSection]) -> list[dict]:
        return [self.section(tab, plan) for plan in plans]

    def section(self, tab: str, plan: OutlineSection) -> dict:
        key = slug(plan.title)
        draft = self.drafted[key]
        tech = unique(key, self.taken)
        ctx = Context(tab=tab, section=tech, repeating=plan.repeatable, taken=self.taken)
        grid: list[dict] = []
        teryt = any(f.kind in TERYT_LEVELS for f in draft.fields)
        teryt_at: Location | None = None  # the section's one TERYT field, once built
        country: str | None = None  # tech of the section's Kraj field
        for field in draft.fields:
            fkey = f"{key}.{slug(field.label)}"
            if teryt and field.kind in TERYT_KINDS:
                if teryt_at is None:  # further levels of the cascade point at this one field
                    teryt_q = teryt_question(ctx, field.required, country)
                    teryt_at = ctx.at(ctx.techs[0])
                    grid.append(teryt_q)
                self.question[fkey] = teryt_q
                self.where[fkey] = teryt_at
                continue
            built = build(field, ctx)
            if field.kind == "country":
                country = ctx.techs[0]
            attr = initial_data.source_for(plan.title, field, plan.repeatable)
            if attr:
                initial_data.apply(built[0]["field"], attr)
            elif self.addressee and initial_data.office_name(plan.title, field):
                built[0]["field"].update(readOnly=True, defaultValue=self.addressee)
            if self.post_build:
                self.post_build(field, built, plan.title)
            self.where[fkey] = ctx.at(ctx.techs[0])
            self.question[fkey] = built[0]
            if field.reusable_list:
                self._propose(field.reusable_list, field.label, field.options, ctx.techs[0])
            grid += built
        section = {"field": make_field(
            "REPEATING_SECTION" if plan.repeatable else "SECTION", tech, draft.title,
            repeatingSectionGrid=grid,
        )}
        self.where[key] = Location(tab, tech, None, plan.repeatable)
        self.question[key] = section
        return section

    def apply_states(self, states: Iterable[StateDraft]) -> None:
        # Several drafts for one target are alternatives ("show when A", "show when B"), but
        # separate SHOW states must all hold, so they become one SHOW state joined with `or`.
        queries: dict[str, list[str]] = {}
        for state in states:
            for leaf in leaves(state.show_when):
                if leaf.field not in self.where:
                    raise DraftError(f"{state.target}: unknown field {leaf.field!r}")
            target = self.where[state.target]
            query = to_jsonata(state.show_when, target, lambda k: self.where[k])
            alternatives = queries.setdefault(state.target, [])
            if query not in alternatives:
                alternatives.append(query)
        for key, alternatives in queries.items():
            query = alternatives[0] if len(alternatives) == 1 else " or ".join(f"({q})" for q in alternatives)
            self.question[key]["field"]["states"].append({"type": "SHOW", "query": query})

    def _propose(self, name: str, label: str, opts: list[str], tech: str) -> None:
        if name in self._proposals:
            self._proposals[name].used_by.append(tech)
            return
        self._proposals[name] = DictionaryProposal(
            name=name, description=f"Lista wartości dla pola „{label}”.", entries=list(opts), used_by=[tech],
        )

    def proposals(self) -> list[DictionaryProposal]:
        return list(self._proposals.values())


def _fields(question: dict):
    yield question["field"]
    for child in question["field"].get("repeatingSectionGrid", []):
        yield from _fields(child)


def _layout(questions: list[dict]) -> list[dict]:
    """One column: sections stack on the tab, fields stack inside them."""
    y = 0
    for q in questions:
        kids = q["field"].setdefault("repeatingSectionGrid", [])
        for i, child in enumerate(kids):
            child.update(x=0, y=i, rows=1, cols=GRID_COLS)
            child["field"]["questionOrder"] = i
        q.update(x=0, y=y, rows=max(1, len(kids)), cols=GRID_COLS)
        q["field"]["questionOrder"] = y
        y += q["rows"]
    return questions


def _id() -> str:
    return str(uuid.uuid4())
