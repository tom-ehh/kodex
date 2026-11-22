"""States for form-specific fields: drafted by a model, compiled to JSONata by code.

After all sections are drafted, the model gets a catalogue of the drafted fields
and returns `StateDraft`s ("show `target` when ..."). The schema for that call is
built per form by `state_set_model`: `target` and `field` are enums of real keys and
each `equals` is limited to that field's options, so constrained decoding cannot
reference a field or option that does not exist. `state_problems` checks the same
for drafts that did not go through that schema (tests, retries).

Nesting is capped at two levels (`A and (B or C)`), enough for the bonus/ examples,
without recursive schemas.

Keys: a section is `<section slug>`, a field `<section slug>.<field slug>`.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal, Union

from pydantic import BaseModel, ConfigDict, Field, create_model

from kodex.schema.drafts import CHOICE_KINDS, Outline, SectionDraft
from kodex.schema.naming import slug

OTHER_OPTION = "Inne"
YES_NO = ("Tak", "Nie")


class _Draft(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Equals(_Draft):
    field: str
    equals: str


class NotEquals(_Draft):
    field: str
    not_equals: str


Leaf = Equals | NotEquals


class AllOfLeaves(_Draft):
    all_of: list[Leaf] = Field(min_length=2)


class AnyOfLeaves(_Draft):
    any_of: list[Leaf] = Field(min_length=2)


class AllOf(_Draft):
    all_of: list[Leaf | AnyOfLeaves] = Field(min_length=2)


class AnyOf(_Draft):
    any_of: list[Leaf | AllOfLeaves] = Field(min_length=2)


Predicate = Leaf | AllOf | AnyOf


class StateDraft(_Draft):
    target: str = Field(description="Klucz sekcji lub pola, które ma się pokazać.")
    show_when: Predicate


class StateSet(_Draft):
    states: list[StateDraft] = []


# --- catalogue -----------------------------------------------------------------------


@dataclass(frozen=True)
class Entry:
    key: str
    label: str
    kind: str  # FieldKind, or "section"
    options: tuple[str, ...]
    section: str  # section key
    repeatable: bool


def field_options(kind: str, options: Iterable[str], other_option: bool) -> tuple[str, ...]:
    if kind == "yes_no":
        return YES_NO
    if kind in CHOICE_KINDS:
        return (*options, OTHER_OPTION) if other_option else tuple(options)
    return ()


def catalogue(outline: Outline, sections: Iterable[SectionDraft]) -> list[Entry]:
    repeatable = {slug(s.title): s.repeatable for s in outline.sections()}
    out: list[Entry] = []
    for section in sections:
        skey = slug(section.title)
        rep = repeatable.get(skey, False)
        out.append(Entry(skey, section.title, "section", (), skey, rep))
        for f in section.fields:
            opts = field_options(f.kind, f.options, f.other_option)
            out.append(Entry(f"{skey}.{slug(f.label)}", f.label, f.kind, opts, skey, rep))
    return out


def describe(entries: list[Entry]) -> str:
    """The catalogue as prompt text, one line per key."""
    lines = []
    for e in entries:
        opts = f" options: {list(e.options)}" if e.options else ""
        rep = " (repeatable)" if e.repeatable else ""
        lines.append(f"{e.key} [{e.kind}]{rep}: {e.label}{opts}")
    return "\n".join(lines)


# --- per-form schema -------------------------------------------------------------------


def state_set_model(entries: list[Entry]) -> type[BaseModel]:
    """A StateSet schema whose keys and option values are enums of this form's fields."""
    sources = [e for e in entries if e.options]
    if not sources:
        return create_model("StateSet", __base__=_Draft, states=(list[StateDraft], Field([], max_length=0)))
    leaves = []
    for i, e in enumerate(sources):
        values = Literal[e.options]  # type: ignore[valid-type]
        key = Literal[e.key]  # type: ignore[valid-type]
        leaves.append(create_model(f"Equals{i}", __base__=_Draft, field=(key, ...), equals=(values, ...)))
        leaves.append(create_model(f"NotEquals{i}", __base__=_Draft, field=(key, ...), not_equals=(values, ...)))
    leaf = Union[tuple(leaves)]  # noqa: UP007 - dynamic union
    all_leaves = create_model("AllOfLeaves", __base__=_Draft, all_of=(list[leaf], Field(min_length=2)))
    any_leaves = create_model("AnyOfLeaves", __base__=_Draft, any_of=(list[leaf], Field(min_length=2)))
    all_of = create_model("AllOf", __base__=_Draft, all_of=(list[Union[leaf, any_leaves]], Field(min_length=2)))  # noqa: UP007
    any_of = create_model("AnyOf", __base__=_Draft, any_of=(list[Union[leaf, all_leaves]], Field(min_length=2)))  # noqa: UP007
    target = Literal[tuple(e.key for e in entries)]  # type: ignore[valid-type]
    state = create_model(
        "StateDraft", __base__=_Draft, target=(target, ...),
        show_when=(Union[leaf, all_of, any_of], ...),  # noqa: UP007
    )
    return create_model("StateSet", __base__=_Draft, states=(list[state], []))


# --- checks ------------------------------------------------------------------------------


def leaves(pred) -> list[Leaf]:
    if isinstance(pred, Equals | NotEquals):
        return [pred]
    items = getattr(pred, "all_of", None) or getattr(pred, "any_of", None) or []
    return [leaf for item in items for leaf in leaves(item)]


def state_problems(states: Iterable[StateDraft], entries: list[Entry]) -> list[str]:
    by_key = {e.key: e for e in entries}
    p = []
    for s in states:
        target = by_key.get(s.target)
        if target is None:
            p.append(f"state target {s.target!r} does not exist")
            continue
        for leaf in leaves(s.show_when):
            source = by_key.get(leaf.field)
            value = leaf.equals if isinstance(leaf, Equals) else leaf.not_equals
            if source is None:
                p.append(f"{s.target}: field {leaf.field!r} does not exist")
            elif not source.options:
                p.append(f"{s.target}: {leaf.field!r} has no options to compare with")
            elif value not in source.options:
                p.append(f"{s.target}: {leaf.field!r} has no option {value!r}")
            elif leaf.field == s.target or source.section == s.target:
                p.append(f"{s.target}: cannot depend on itself; a section cannot be hidden by a "
                         f"question inside it, put the state on its other fields instead")
            elif source.repeatable and source.section != target.section:
                p.append(f"{s.target}: cannot depend on {leaf.field!r} inside a repeatable section")
    return p


def repair_states(states: Iterable[StateDraft], entries: list[Entry]) -> list[StateDraft]:
    """Move a section's state that its own question controls onto that section's fields.

    Models often say "show 'Dane pełnomocnika' when 'Dane pełnomocnika.Rodzaj' = ...":
    hiding the section would hide the question that shows it again. The intent is that
    the rest of the section depends on the question, so the state goes to every other
    field of the section that has no state of its own yet.
    """
    states = list(states)
    sections = {e.key for e in entries if e.kind == "section"}
    targeted = {s.target for s in states}
    out: list[StateDraft] = []
    for s in states:
        sources = {leaf.field for leaf in leaves(s.show_when)}
        inside = {k for k in sources if k.startswith(f"{s.target}.")}
        if s.target not in sections or not inside:
            out.append(s)
            continue
        for e in entries:
            if e.section == s.target and e.kind != "section" and e.key not in sources | targeted:
                out.append(StateDraft(target=e.key, show_when=s.show_when))
                targeted.add(e.key)
    return out


# --- JSONata -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Location:
    tab: str  # tab title
    section: str  # section techName
    field: str | None  # field techName; None for the section itself
    repeating: bool


def _quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _tab(title: str) -> str:
    return title.replace("\\", "\\\\").replace("'", "\\'")


def path(source: Location, target: Location) -> str:
    base = f"tabs['{_tab(source.tab)}' in title].questions['{source.section}' in field.techName].field"
    if source.repeating and target.repeating and target.section == source.section:
        return f"{base}.__userAnswer[$repeatingSectionIndex]['{source.field}' in fieldTechName].answers"
    return f"{base}.repeatingSectionGrid['{source.field}' in field.techName].field.__userAnswer"


def to_jsonata(pred, target: Location, locate: Callable[[str], Location], nested: bool = False) -> str:
    if isinstance(pred, Equals):
        return f'("{_quote(pred.equals)}" in {path(locate(pred.field), target)})'
    if isinstance(pred, NotEquals):
        return f'$not("{_quote(pred.not_equals)}" in {path(locate(pred.field), target)})'
    op, items = ("and", pred.all_of) if hasattr(pred, "all_of") else ("or", pred.any_of)
    text = "\nor\n" if op == "or" else "\nand\n"
    body = text.join(to_jsonata(i, target, locate, nested=True) for i in items)
    return f"({body})" if nested else body
