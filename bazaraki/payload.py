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
# reference (`I[...]`) are skipped; a text row announces itself with `T<len>,`
# and is read by that length, because its text may hold newlines and the row
# after it therefore starts wherever the text stops rather than on a fresh
# line. That is why rows are walked in order instead of found by a regex over
# line starts: one advert description is enough to hide every row behind it.
_ROW = re.compile(r"([0-9a-f]+):")
_TEXT = re.compile(r"T([0-9a-f]+),")

# What an object writes in place of a text row it owns: `"$1f"` is "the string
# sent as row 1f". `$undefined` and `$L1d` are other things and are left alone.
_REFERENCE = re.compile(r"\$([0-9a-f]+)")


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


def _next_line(stream: str, at: int) -> int:
    """Give up on the current row and resume at the next one."""
    newline = stream.find("\n", at)
    return len(stream) if newline == -1 else newline + 1


def _text_end(stream: str, start: int, length: int) -> int:
    """Where a text row ends. ``length`` counts UTF-8 bytes, not characters —
    a Greek advert is two bytes a letter and would otherwise be cut in half."""
    end, spent = start, 0
    while spent < length and end < len(stream):
        spent += len(stream[end].encode("utf-8"))
        end += 1
    return end


def _rows(stream: str) -> Iterator[tuple[str, Any, bool]]:
    """Every row of a flight stream in document order, as (id, value, is_text).

    Text rows are handed back as their raw string; JSON rows as their decoded
    value; anything else (module references, malformed rows) is skipped.
    """
    at = 0
    while at < len(stream):
        if stream[at] == "\n":
            at += 1
            continue
        header = _ROW.match(stream, at)
        if header is None:
            at = _next_line(stream, at)
            continue
        text = _TEXT.match(stream, header.end())
        if text is not None:
            end = _text_end(stream, text.end(), int(text.group(1), 16))
            yield header.group(1), stream[text.end():end], True
            at = end
            continue
        try:
            value, at = _DECODER.raw_decode(stream, header.end())
        except ValueError:
            at = _next_line(stream, header.end())
            continue
        yield header.group(1), value, False


def row_values(stream: str) -> Iterator[Any]:
    """Decode each JSON-valued row of a flight stream, skipping the rest."""
    for _, value, is_text in _rows(stream):
        if not is_text:
            yield value


def _resolved(node: Any, texts: dict[str, str]) -> Any:
    """Put the text rows back where the object only points at them.

    A long string is not written inside the object that owns it: the stream
    sends it as its own text row and leaves `"$<row id>"` behind. An advert's
    description is always long enough to be sent that way, so an object taken
    from the stream as it stands says `"$23"` where the seller's words should
    be — and a filter reading the seller's words would never match.
    """
    if isinstance(node, dict):
        return {key: _resolved(value, texts) for key, value in node.items()}
    if isinstance(node, list):
        return [_resolved(value, texts) for value in node]
    if isinstance(node, str):
        reference = _REFERENCE.fullmatch(node)
        if reference and reference.group(1) in texts:
            return texts[reference.group(1)]
    return node


def find(soup: BeautifulSoup, *keys: str) -> dict | None:
    """First object anywhere in the page payload that has all of ``keys``.

    Rows are addressed by build-specific ids and the interesting objects are
    sometimes nested (the advert detail arrives inside a dehydrated react-query
    cache), so objects are identified by their shape instead of their position.
    Text rows the object references are spliced into what is returned.
    """
    wanted = set(keys)
    rows = list(_rows(flight_stream(soup)))
    texts = {row_id: value for row_id, value, is_text in rows if is_text}
    for _, value, is_text in rows:
        if is_text:
            continue
        stack: list[Any] = [value]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if wanted <= node.keys():
                    return _resolved(node, texts)
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    return None
