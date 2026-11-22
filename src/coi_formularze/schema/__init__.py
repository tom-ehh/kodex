"""Form Definition output contract as Pydantic models.

- `code`: pieces code fills (grid layout, ids, tabs, flow).
- `llm`: pieces the LLM fills (section headers, the 11 field types, states).
- `form`: the full `FormDefinition` joining both, with the cross-reference rules.

`json_schemas()` exports JSON Schema for each piece (e.g. for LLM structured output).
"""

from typing import Any

from pydantic import BaseModel

from coi_formularze.schema import code, llm
from coi_formularze.schema.form import (
    FormDefinition,
    FormIssue,
    validate_form_definition,
)

PIECES: dict[str, type[BaseModel]] = {
    "form_definition": FormDefinition,
    "code.layout": code.Layout,
    "code.placement": code.Placement,
    "code.tab_header": code.TabHeader,
    "code.flow": code.Flow,
    "llm.section_header": llm.SectionHeader,
    **{f"llm.{c.__name__.lower()}": c for c in llm.FIELD_TYPES},
}


def json_schemas() -> dict[str, dict[str, Any]]:
    return {name: model.model_json_schema() for name, model in PIECES.items()}


__all__ = ["PIECES", "FormDefinition", "FormIssue", "json_schemas", "validate_form_definition"]
