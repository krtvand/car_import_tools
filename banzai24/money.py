"""Yen amounts as text. One formatter, so every number on the page matches."""

from __future__ import annotations


def format_yen(v: int | None) -> str | None:
    """A yen price split as "¥ 1849 000" — thousands, space, the last three digits.

    The bidding platform takes a bid in thousands of yen, so the number you
    actually type is the group before the space. Grouping the usual way
    ("¥1,849,000") means doing that division in your head at the one moment you
    can least afford to get it wrong. Under ¥1000 there is nothing to split, so
    those print plain.
    """
    if v is None:
        return None
    thousands, rest = divmod(abs(v), 1000)
    sign = "-" if v < 0 else ""
    if not thousands:
        return f"¥{sign}{rest}"
    return f"¥ {sign}{thousands} {rest:03d}"
