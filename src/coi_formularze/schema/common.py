"""Shared building blocks of the Form Definition.

Sources: the technical rules doc and the field description doc in bonus/; example
outputs only where they agree. Attribute names are the JSON keys (camelCase), with
the organiser typos fixed (repeatingSectionGrid). Unknown keys are kept: the
platform emits extras such as multiLevelTree.
"""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

NonEmpty = Annotated[str, StringConstraints(min_length=1)]


class Model(BaseModel):
    model_config = ConfigDict(extra="allow")


class Validator(StrEnum):
    """The only validators the rules allow."""

    REQUIRED = "REQUIRED"
    EMAIL = "EMAIL"
    PESEL = "PESEL"
    NIP = "NIP"
    REGON = "REGON"
    ZIP_CODE = "ZIP_CODE"
    EPUAP = "EPUAP"
    ADE = "ADE"
    SERIAL_NUMBER_ID_CARD = "SERIAL_NUMBER_ID_CARD"


class StateType(StrEnum):
    SHOW = "SHOW"
    READ_ONLY = "READ_ONLY"


class State(Model):
    """Dynamic state (rule 8): when `query` (JSONata) holds, apply `type`."""

    type: StateType
    query: NonEmpty


class Option(Model):
    id: NonEmpty
    name: NonEmpty
    techName: NonEmpty
