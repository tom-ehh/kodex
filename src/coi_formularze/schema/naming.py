"""Tech names: ASCII snake_case slugs of Polish labels, as the platform uses them."""

import re
import unicodedata

_POLISH = str.maketrans({"ł": "l", "Ł": "L"})


def slug(text: str) -> str:
    """'Wybierz, co mamy zrobić z nadpłatą' -> 'wybierz_co_mamy_zrobic_z_nadplata'."""
    ascii_text = (
        unicodedata.normalize("NFKD", text.translate(_POLISH))
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    return re.sub(r"[^a-z0-9]+", "_", ascii_text.lower()).strip("_") or "pole"


def unique(base: str, taken: set[str], qualifier: str = "") -> str:
    """Return `base`, or a variant not in `taken`; record the result in `taken`."""
    candidates = [base]
    if qualifier:
        candidates.append(f"{base}_{qualifier}")
    for name in candidates:
        if name not in taken:
            taken.add(name)
            return name
    n = 2
    while f"{candidates[-1]}_{n}" in taken:
        n += 1
    name = f"{candidates[-1]}_{n}"
    taken.add(name)
    return name
