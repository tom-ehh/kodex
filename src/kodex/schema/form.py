"""The full Form Definition: code pieces (code.py) + LLM pieces (llm.py) joined.

Rules enforced here that no single piece can see: tab order and the FINISH tab
(rules 2, 5), unique ids and techNames, flow links that join existing tabs and reach
every tab (rule 7).
"""

from collections import Counter
from collections.abc import Iterator
from typing import Annotated, Any, Self

from pydantic import ConfigDict, Field, ValidationError, model_validator

from kodex.schema import llm
from kodex.schema.code import Flow, Layout, Placement, TabHeader, TabType
from kodex.schema.common import Model


def _placed(cls: type[llm.FieldBase]) -> type:
    """The LLM field `cls` plus its code-filled id and questionOrder."""
    return type(cls.__name__, (cls, Placement), {
        "__module__": __name__, "__doc__": cls.__doc__, "model_config": ConfigDict(extra="allow"),
    })


TextField, Textarea, Number, Datepicker, FileUploader, Checkbox, Radio, Select, TextBlock, \
    ConsentSection, InfoSection = (_placed(c) for c in llm.FIELD_TYPES)

Field_ = Annotated[
    TextField | Textarea | Number | Datepicker | FileUploader | Checkbox | Radio | Select  # type: ignore[valid-type]
    | TextBlock | ConsentSection | InfoSection,
    Field(discriminator="type"),
]


class ChildQuestion(Layout):
    """Inside a section: any non-section component (no nesting, rule 4)."""

    field: Field_


class Section(llm.SectionHeader, Placement):
    model_config = ConfigDict(extra="allow")
    repeatingSectionGrid: list[ChildQuestion] = Field(default_factory=list)


class TopQuestion(Layout):
    """Directly on a tab: always a section (rule 4)."""

    field: Section


class Tab(TabHeader):
    questions: list[TopQuestion] = Field(min_length=1)  # rule 3

    @model_validator(mode="after")
    def _placement_rules(self) -> Self:
        children = [c.field for q in self.questions for c in q.field.repeatingSectionGrid]
        if not children:
            raise ValueError("the tab holds no field (rule 3)")
        if self.type == TabType.FINISH and any(f.type != "INFO_SECTION" for f in children):
            raise ValueError("the FINISH tab may hold only INFO_SECTION, inside sections (rules 4, 5)")
        return self


class FormDefinition(Model):
    title: str = Field(min_length=1, max_length=255)  # rule 1
    tabs: list[Tab] = Field(min_length=2)  # rule 2
    flow: Flow

    @model_validator(mode="after")
    def _cross_checks(self) -> Self:
        problems = list(_cross_checks(self))
        if problems:
            raise ValueError("\n".join(problems))
        return self

    def fields(self) -> Iterator[Any]:
        """Every field on every tab, sections first, then their children."""
        for tab in self.tabs:
            for q in tab.questions:
                yield q.field
                if isinstance(q.field, Section):
                    yield from (c.field for c in q.field.repeatingSectionGrid)


def _cross_checks(form: FormDefinition) -> Iterator[str]:
    last = len(form.tabs) - 1
    for i, tab in enumerate(form.tabs):
        want = TabType.FINISH if i == last else TabType.MAIN
        if tab.type != want:
            yield f"tabs.{i}.type: expected {want}: the last tab is FINISH, the others MAIN"

    ids = Counter([t.id for t in form.tabs] + [f.id for f in form.fields()])
    tech = Counter(f.techName for f in form.fields())
    yield from (f"id {v!r} is used {n} times" for v, n in ids.items() if n > 1)
    yield from (f"techName {v!r} is used {n} times" for v, n in tech.items() if n > 1)

    tab_ids = [t.id for t in form.tabs]
    edges: dict[str, set[str]] = {t: set() for t in tab_ids}
    for i, link in enumerate(form.flow.links):
        bad = [end for end in ("source", "target") if getattr(link, end) not in edges]
        yield from (f"flow.links.{i}.{end}: {getattr(link, end)!r} is not a tab id" for end in bad)
        if not bad:
            edges[link.source].add(link.target)
    seen, stack = {tab_ids[0]}, [tab_ids[0]]
    while stack:
        for nxt in edges[stack.pop()] - seen:
            seen.add(nxt)
            stack.append(nxt)
    yield from (f"tabs.{i}: not reachable from the first tab through flow links"
                for i, t in enumerate(tab_ids) if t not in seen)


class FormIssue(Model):
    path: str  # dotted location, "" for the whole document
    message: str


def validate_form_definition(doc: Any) -> list[FormIssue]:
    """Every issue found in `doc`; an empty list means it is a valid Form Definition."""
    try:
        FormDefinition.model_validate(doc)
    except ValidationError as exc:
        return [
            FormIssue(path=".".join(str(p) for p in err["loc"]), message=line)
            for err in exc.errors()
            for line in str(err["msg"]).removeprefix("Value error, ").split("\n")
        ]
    return []
