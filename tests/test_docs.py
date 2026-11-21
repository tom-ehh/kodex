"""Docs completeness: README.md must list every endpoint, setting and accepted extension.

Details (issue codes, MIME types, schemas) live in the OpenAPI spec at /docs."""

from pathlib import Path

import pytest

from coi_formularze.config import Settings
from coi_formularze.main import create_app
from coi_formularze.validation.sniff import FORMATS

README = (Path(__file__).parent.parent / "README.md").read_text(encoding="utf-8")

ROUTES = [
    (method, path)
    for path, ops in create_app().openapi()["paths"].items()
    for method in ops
]


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_every_endpoint_documented(method, path):
    assert f"`{method.upper()}` | `{path}`" in README, f"{method.upper()} {path} missing from README.md"



@pytest.mark.parametrize("spec", FORMATS.values(), ids=lambda s: s.kind)
def test_every_extension_documented(spec):
    for ext in spec.extensions:
        assert f"`{ext}`" in README


@pytest.mark.parametrize("field", Settings.model_fields)
def test_every_setting_documented(field):
    assert f"`COI_{field.upper()}`" in README
