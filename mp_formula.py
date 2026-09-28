"""Chemical formula normalisation shared by the index builder and the query parser.

Materials Project stores ``formula_pretty`` using its own element ordering.  Users
often type the textbook form instead (``NiOOH`` while MP writes ``NiHO2``).  We
reduce the composition to its smallest integer ratio and sort with the Hill
system (C first, H second, everything else alphabetically), so that

    NiOOH  /  NiHO2  /  HNiO2   ->   HNiO2

The same function is used when building the ``formula_keys`` table and when
parsing a query, which guarantees the two sides always agree.
"""
from __future__ import annotations

import warnings

try:  # pymatgen is available in the reference environment; degrade gracefully if not.
    from pymatgen.core import Composition, Element
except Exception:  # noqa: BLE001
    Composition = None  # type: ignore[assignment]
    Element = None  # type: ignore[assignment]


def formula_key(text: str | None) -> str:
    """Return the Hill-ordered reduced key for a formula, or '' when not parseable."""
    if Composition is None or Element is None:
        return ""
    text = (text or "").strip()
    if not text:
        return ""
    try:
        composition = Composition(text)
    except Exception:  # noqa: BLE001 - any parse failure means "not a formula"
        return ""
    if not composition:
        return ""
    # pymatgen would happily parse "Zz9Qq" into a DummySpecies; only accept real elements.
    for element in composition.elements:
        if not isinstance(element, Element):
            return ""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # noble-gas electronegativity warnings
            reduced = composition.reduced_composition
    except Exception:  # noqa: BLE001
        reduced = composition

    items: list[tuple[str, float]] = []
    for element, amount in reduced.items():
        symbol = element.symbol if isinstance(element, Element) else str(element)
        items.append((symbol, float(amount)))
    # Hill ordering: carbon, hydrogen, then the rest alphabetically.
    items.sort(key=lambda item: (item[0] != "C", item[0] != "H", item[0]))

    parts: list[str] = []
    for symbol, amount in items:
        rounded = round(amount)
        if abs(amount - rounded) < 1e-6:
            count = int(rounded)
            parts.append(symbol if count == 1 else f"{symbol}{count}")
        else:
            parts.append(f"{symbol}{amount:g}")
    return "".join(parts)


def element_symbol(token: str) -> str | None:
    """Normalise a single element token (case-insensitive) to its canonical symbol."""
    if Element is None or not token:
        return None
    token = token.strip()
    if not 1 <= len(token) <= 2 or not token.isalpha():
        return None
    candidate = token[0].upper() + token[1:].lower()
    try:
        element = Element(candidate)
    except Exception:  # noqa: BLE001
        return None
    return element.symbol
