"""``python -m searches`` — see what the saved searches say, and whether they parse.

Two commands, and the difference between them is who is asking. ``list`` is for
a person deciding what to run this morning. ``check`` is for a script about to
spend money: it parses every file and exits non-zero if any of them is broken,
which is cheap to run before a fetch and much cheaper than finding out halfway
through one.
"""
from __future__ import annotations

import argparse

from . import definition


def _bid_line(band) -> str:
    prices = ", ".join(
        f"{kind} ¥{value:,}" for kind, value in sorted(band.max_bid_jpy.items()))
    return f"{band.label} → {prices}"


def _print_search(name: str, search) -> None:
    print(f"{name}: {search.describe()}")
    for band in search.bands:
        print(f"    {_bid_line(band)}")
        profit = search.profit_for(band)
        detail = (f"competitors {band.competitors.describe()}"
                  if band.competitors.declared else "competitors: no bounds declared")
        if phrases := band.competitors.exclude_phrases:
            # This band's own, on top of the search's — printed here rather than
            # merged into the line below, so it is clear which band drops them.
            detail += f" · not saying {', '.join(phrases)}"
        print(f"      {detail}"
              + (f" · profit {profit.describe()}" if profit is not None else " · no profit set"))
    if search.competitors.declared:
        print(f"    all bands: {search.competitors.describe()}")
    for section in ("site", "api", "sheet"):
        body = search.sections.get(section) or {}
        if body:
            shown = ", ".join(f"{key}={value}" for key, value in sorted(body.items()))
            print(f"    [{section}] {shown}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m searches",
        description="The saved searches: one file per car, read by every module.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="Describe every saved search")
    sub.add_parser("check", help="Parse every saved search; non-zero exit if any is broken")

    args = parser.parse_args(argv)
    entries = definition.load_all()

    if not entries:
        print(f"No searches in {definition.SEARCH_DIR}")
        return 1

    broken = 0
    for name, search, problem in entries:
        if problem:
            broken += 1
            print(f"{name}: BROKEN — {problem}")
        elif args.command == "list":
            _print_search(name, search)
        else:
            print(f"{name}: ok — {search.describe()}")

    if broken:
        print(f"\n{broken} of {len(entries)} searches will not load.")
    return 1 if broken else 0
