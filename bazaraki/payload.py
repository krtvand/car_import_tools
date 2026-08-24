"""Read the JSON payload that bazaraki embeds in every page.

bazaraki.com is a Next.js (App Router) site: the markup it serves is generated
from a React Server Component "flight" stream, inlined as a series of
``self.__next_f.push([1, "<chunk>"])`` scripts. Concatenating those chunks
yields a stream of ``<hex id>:<value>`` rows, and among them sit plain-JSON
rows carrying exactly what the page shows — the advert list with prices and
features, the filter option codes, the advert detail.

Those rows are the site's own API responses, so reading them is far steadier
than scraping the rendered DOM, which is machine-generated Tailwind with no
stable hooks to hang a selector on.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterator

from bs4 import BeautifulSoup

_DECODER = json.JSONDecoder()

# Every data-carrying script is `self.__next_f.push([1,"<js string>"])`; the
# chunk is read with raw_decode rather than a regex so backslash escapes inside
# it (the payload is full of them) can't end the match early.
_PUSH = "self.__next_f.push([1,"

# A row header: hex id, colon, then the value. Rows whose value is a module
# reference (`I[...]`) or a text blob (`T<len>,...`) simply fail to decode.
_ROW = re.compile(r"(?m)^[0-9a-f]+:")


def _chunks(script: str) -> Iterator[str]:
    at = 0
    while (found := script.find(_PUSH, at)) != -1:
        at = found + len(_PUSH)
        try:
            chunk, end = _DECODER.raw_decode(script, at)
        except ValueError:
            continue
        if isinstance(chunk, str):
            yield chunk
            at = end


def flight_stream(soup: BeautifulSoup) -> str:
    """The page's whole flight stream, chunks reassembled in document order."""
    parts: list[str] = []
    for script in soup.find_all("script"):
        text = script.string or script.get_text()
        if text and _PUSH in text:
            parts.extend(_chunks(text))
    return "".join(parts)


def row_values(stream: str) -> Iterator[Any]:
    """Decode each JSON-valued row of a flight stream, skipping the rest."""
    for match in _ROW.finditer(stream):
        try:
            value, _ = _DECODER.raw_decode(stream, match.end())
        except ValueError:
            continue
        yield value


def find(soup: BeautifulSoup, *keys: str) -> dict | None:
    """First object anywhere in the page payload that has all of ``keys``.

    Rows are addressed by build-specific ids and the interesting objects are
    sometimes nested (the advert detail arrives inside a dehydrated react-query
    cache), so objects are identified by their shape instead of their position.
    """
    wanted = set(keys)
    for value in row_values(flight_stream(soup)):
        stack: list[Any] = [value]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if wanted <= node.keys():
                    return node
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    return None
