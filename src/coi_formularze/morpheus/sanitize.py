"""Value → shape, and the PII lint every stored Morpheus record must pass (pure functions)."""

import re
from collections.abc import Iterable
from typing import Any

# Shape alphabet uses no digits, so a shape can never itself look like PII.
_SHAPE = {"digit": "#", "upper": "A", "lower": "a"}


def shape(value: str, max_len: int = 64) -> str:
    """Replace digits with #, letters with A/a, keep punctuation and spaces: '00-950' → '##-###'."""
    out = []
    for ch in value[:max_len]:
        if ch.isdigit():
            out.append(_SHAPE["digit"])
        elif ch.isalpha():
            out.append(_SHAPE["upper"] if ch.isupper() else _SHAPE["lower"])
        elif ch.isspace():
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out) + ("…" if len(value) > max_len else "")


_PII = {
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "nip": re.compile(r"\b\d{3}-\d{3}-\d{2}-\d{2}\b|\b\d{3}-\d{2}-\d{2}-\d{3}\b"),
    "postcode": re.compile(r"\b\d{2}-\d{3}\b"),
    "phone": re.compile(r"(?:\+\d{2}[\s-]?)?\b\d{3}[\s-]\d{3}[\s-]\d{3}\b"),
    # PESEL (11), REGON (9/14), NIP without dashes (10), account numbers, …
    "digit_run": re.compile(r"\d{6,}"),
}

# Metadata keys that legitimately hold digits (timestamps, generated ids).
SKIP_KEYS = frozenset(
    {"id", "skill_id", "created_at", "expires_at", "ts", "episode_ids", "form_id", "ref_id"}
)


def pii_findings(value: Any, path: str = "$") -> list[str]:
    """Paths + kinds of PII-shaped literals anywhere in a JSON-like value."""
    found: list[str] = []
    if isinstance(value, str):
        found += [f"{path}: {kind}" for kind, rx in _PII.items() if rx.search(value)]
    elif isinstance(value, dict):
        for k, v in value.items():
            if k not in SKIP_KEYS:
                found += pii_findings(v, f"{path}.{k}")
    elif isinstance(value, list | tuple):
        for i, v in enumerate(value):
            found += pii_findings(v, f"{path}[{i}]")
    return found


class PIIError(ValueError):
    """A record contains a literal that looks like personal data; it is not stored."""

    def __init__(self, findings: Iterable[str]):
        self.findings = list(findings)
        super().__init__("PII-shaped literal(s): " + "; ".join(self.findings))


def assert_clean(value: Any) -> None:
    if findings := pii_findings(value):
        raise PIIError(findings)
