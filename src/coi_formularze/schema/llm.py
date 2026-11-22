"""Pieces filled by the LLM from the input form: section headers, fields, states.

None of these carry ids or grid placement; code adds those (see code.py) and
form.py joins both into the final shape.
"""

from typing import Annotated, Any, ClassVar, Literal, Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from coi_formularze.schema.common import (
    Model,
    NonEmpty,
    Option,
    State,
    StateType,
    Validator,
)


class FieldBase(Model):
    """Keys every component may carry (field description doc).

    Unknown keys are rejected so a misspelt or invented key from the LLM fails here;
    the joined models in form.py accept them again (the platform emits extras).
    """

    model_config = ConfigDict(extra="forbid")

    techName: NonEmpty
    title: str | None = None
    description: str | None = None
    tooltipMessage: str | None = None
    readOnly: bool | None = None
    defaultValue: str | list[Any] | None = None  # rules show [] for SELECT/TEXTAREA/FILE_UPLOADER
    defaultValueType: Literal["EXTERNAL_SYSTEM", ""] | None = None
    states: list[State] | None = None

    allows_read_only: ClassVar[bool] = False  # READ_ONLY states are defined for input fields only

    @field_validator("states")
    @classmethod
    def _states_allowed(cls, states: list[State] | None) -> list[State] | None:
        if states and not cls.allows_read_only and any(s.type != StateType.SHOW for s in states):
            raise ValueError("only SHOW states here; READ_ONLY is for input fields")
        return states

    @model_validator(mode="after")
    def _no_children(self) -> Self:
        """Only sections hold fields (rule 4)."""
        if getattr(self, "repeatingSectionGrid", None):
            raise ValueError("only SECTION / REPEATING_SECTION may hold repeatingSectionGrid")
        return self


class PlainField(FieldBase):
    dictionaryName: Literal[""] | None = Field(
        None, description="Rule 9: dictionaries only on SELECT, CHECKBOX, RADIO."
    )


RequiredOnly = list[Literal[Validator.REQUIRED]]


class InputField(PlainField):
    """Rule 6: every input field states if it is required (REQUIRED inside).
    Only TEXT_FIELD may take any validator; the others take only the required one."""

    allows_read_only: ClassVar[bool] = True
    validation: RequiredOnly


class ChoiceField(FieldBase):
    """CHECKBOX / RADIO / SELECT: own options, or a dictionary with empty options."""

    allows_read_only: ClassVar[bool] = True
    validation: RequiredOnly
    options: list[Option]
    dictionaryName: str | None = None

    @model_validator(mode="after")
    def _options_or_dictionary(self) -> Self:
        if self.dictionaryName and self.options:
            raise ValueError("with a dictionaryName, options must be empty")
        if not self.dictionaryName and not self.options:
            raise ValueError("needs options or a dictionaryName")
        return self


class TextField(InputField):
    type: Literal["TEXT_FIELD"]
    validation: list[Validator]  # "dowolny walidator z listy"


class Textarea(InputField):
    type: Literal["TEXTAREA"]


class Number(InputField):
    type: Literal["NUMBER"]


class Datepicker(InputField):
    type: Literal["DATEPICKER"]


class FileUploader(InputField):
    type: Literal["FILE_UPLOADER"]


class Checkbox(ChoiceField):
    type: Literal["CHECKBOX"]


class Radio(ChoiceField):
    type: Literal["RADIO"]


class Select(ChoiceField):
    type: Literal["SELECT"]
    searchEnabled: bool | None = None


class TextBlock(PlainField):
    type: Literal["TEXT_BLOCK"]


class Consent(Model):
    description: NonEmpty
    required: bool


class ConsentSectionConfig(Model):
    consents: list[Consent] = Field(min_length=1)
    required: bool | None = None
    selectAllEnabled: bool | None = None
    files: list[Any] | None = None


class ConsentSection(PlainField):
    type: Literal["CONSENT_SECTION"]
    consentSectionConfig: ConsentSectionConfig
    selectAllEnabled: bool | None = None


class InfoSection(PlainField):
    """The only component on the FINISH tab, inside a section (rules 4, 5)."""

    type: Literal["INFO_SECTION"]
    description: NonEmpty


class SectionHeader(PlainField):
    """SECTION / REPEATING_SECTION without its fields; form.Section adds them."""

    type: Literal["SECTION", "REPEATING_SECTION"]
    title: NonEmpty

    @model_validator(mode="after")
    def _no_children(self) -> Self:  # sections may hold fields
        return self


FIELD_TYPES = (TextField, Textarea, Number, Datepicker, FileUploader, Checkbox, Radio, Select,
               TextBlock, ConsentSection, InfoSection)
LlmField = Annotated[
    TextField | Textarea | Number | Datepicker | FileUploader | Checkbox | Radio | Select
    | TextBlock | ConsentSection | InfoSection,
    Field(discriminator="type"),
]
