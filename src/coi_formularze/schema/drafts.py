"""What a language model fills, pass by pass, via structured output. Small and flat.

No ids, grid positions, question order, tech names, widgets or JSONata: code owns
those (kinds.py, states.py, assemble.py). Every rule here gives a readable error the
caller feeds back on a retry.

Passes, in order:

1. `FormFacts`: title, addressee, who may file. Context for the later passes only.
2. `Outline`: every page and section of the form, in order, each with a purpose.
3. `SectionDraft`: one per outlined section, with fields described by *kind*.
4. States: see states.py (schema built per form from the drafted fields).
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from coi_formularze.schema.common import Validator
from coi_formularze.schema.naming import slug

FieldKind = Literal[
    "text", "long_text", "number", "date", "file", "info",
    "single_choice", "multi_choice", "yes_no",
    "pesel", "nip", "regon", "krs", "email", "phone", "zip_code",
    "country", "voivodeship", "county", "commune", "locality",
]  # fmt: skip
CHOICE_KINDS = frozenset({"single_choice", "multi_choice"})

TITLE_STARTS = ("Wniosek", "Zgłoszenie", "Deklaracja", "Oświadczenie")
Filer = Literal["person", "entity"]


class _Draft(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --- 1. FormFacts --------------------------------------------------------------------


class FormFacts(_Draft):
    title: str = Field(
        max_length=255,
        description="Tytuł usługi dla obywatela: zaczyna się od Wniosek/Zgłoszenie/"
        "Deklaracja/Oświadczenie, krótki, bez odwołań do przepisów.",
    )
    addressee: str = Field(
        min_length=1, description="Adresat dokładnie jak na formularzu, np. 'Wójt Gminy Elbląg'."
    )
    filers: list[Filer] = Field(
        min_length=1,
        description="Kto może złożyć wniosek: osoba fizyczna (person) i/lub firma lub "
        "instytucja (entity, np. pola 'nazwa', 'NIP', 'KRS').",
    )

    @field_validator("title")
    @classmethod
    def _title_start(cls, v: str) -> str:
        if not v.startswith(TITLE_STARTS):
            raise ValueError(f"title must start with one of {', '.join(TITLE_STARTS)}")
        return v

    @field_validator("filers")
    @classmethod
    def _unique_filers(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("filers must not repeat")
        return v


# --- 2. Outline ------------------------------------------------------------------------


class OutlineSection(_Draft):
    title: str = Field(min_length=1, max_length=120)
    purpose: str = Field(min_length=1, description="Jedno zdanie: jakie dane zbiera sekcja.")
    repeatable: bool = Field(
        False, description="Czy obywatel wypełnia sekcję wiele razy (np. kilka psów)."
    )


class OutlinePage(_Draft):
    title: str = Field(min_length=1, max_length=120)
    sections: list[OutlineSection] = Field(min_length=1)


class Outline(_Draft):
    pages: list[OutlinePage] = Field(
        min_length=1, description="Strony formularza w kolejności, bez strony podsumowania."
    )

    def sections(self) -> list[OutlineSection]:
        return [s for page in self.pages for s in page.sections]

    @model_validator(mode="after")
    def _unique(self):
        slugs = [slug(s.title) for s in self.sections()]
        dupes = sorted({s for s in slugs if slugs.count(s) > 1})
        if dupes:
            raise ValueError(f"section titles must be unique: {dupes}")
        pages = [slug(p.title) for p in self.pages]
        if len(set(pages)) != len(pages):
            raise ValueError("page titles must be unique")
        return self


# --- 3. SectionDraft -------------------------------------------------------------------


class FieldDraft(_Draft):
    label: str = Field(min_length=1, max_length=200, description="Etykieta pola po polsku.")
    kind: FieldKind
    required: bool
    options: list[str] = Field(default=[], description="Tylko dla single_choice/multi_choice.")
    other_option: bool = Field(False, description="Dodaje opcję 'Inne' z polem do wpisania.")
    help: str | None = Field(None, description="Krótka podpowiedź pod etykietą.")
    extra_validators: list[Validator] = Field(default=[], description="Tylko dla text.")
    reusable_list: str | None = Field(
        None,
        pattern=r"^[A-Z][A-Z0-9_]*$",
        description="Nazwa proponowanego słownika, gdy opcje to lista ogólna (np. rasy psów).",
    )

    @model_validator(mode="after")
    def _kind_rules(self):
        choice = self.kind in CHOICE_KINDS
        if choice and len(self.options) < 2:
            raise ValueError(f"{self.label!r}: {self.kind} needs at least 2 options")
        if not choice and self.options:
            raise ValueError(f"{self.label!r}: options are only for single_choice/multi_choice")
        if len({o.casefold() for o in self.options}) != len(self.options):
            raise ValueError(f"{self.label!r}: options must not repeat")
        if self.other_option and not choice:
            raise ValueError(f"{self.label!r}: other_option is only for choice kinds")
        if self.other_option and any(o.casefold() == "inne" for o in self.options):
            raise ValueError(f"{self.label!r}: use other_option instead of an 'Inne' option")
        if self.extra_validators and self.kind != "text":
            raise ValueError(f"{self.label!r}: extra_validators are only for text")
        if Validator.REQUIRED in self.extra_validators:
            raise ValueError(f"{self.label!r}: use required=true instead of a REQUIRED validator")
        if self.reusable_list and not choice:
            raise ValueError(f"{self.label!r}: reusable_list is only for choice kinds")
        return self


class SectionDraft(_Draft):
    title: str = Field(min_length=1, max_length=120)
    fields: list[FieldDraft] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_labels(self):
        slugs = [slug(f.label) for f in self.fields]
        dupes = sorted({s for s in slugs if slugs.count(s) > 1})
        if dupes:
            raise ValueError(f"field labels must be unique within a section: {dupes}")
        return self
