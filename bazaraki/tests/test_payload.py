"""Tests for reading the JSON payload embedded in bazaraki pages."""
from __future__ import annotations

import json

from bs4 import BeautifulSoup

from bazaraki import payload


def _page(*chunks: str) -> BeautifulSoup:
    scripts = "".join(
        f"<script>self.__next_f.push([1,{json.dumps(c)}])</script>" for c in chunks
    )
    return BeautifulSoup(f"<html><body>{scripts}</body></html>", "html.parser")


def test_flight_stream_reassembles_chunks_split_mid_value():
    soup = _page('1:{"a":', '1,"b":2}\n')
    assert payload.flight_stream(soup) == '1:{"a":1,"b":2}\n'


def test_flight_stream_ignores_scripts_without_a_push():
    soup = BeautifulSoup(
        '<html><body><script>window.x = 1;</script></body></html>', "html.parser"
    )
    assert payload.flight_stream(soup) == ""


def test_flight_stream_keeps_a_chunk_containing_the_push_marker():
    # A chunk that quotes the marker must not be mistaken for a second push.
    soup = _page('1:"self.__next_f.push([1,\\"nope\\"])"\n')
    assert payload.flight_stream(soup) == '1:"self.__next_f.push([1,\\"nope\\"])"\n'


def test_row_values_skips_module_and_text_rows():
    stream = '1:I[9766,[],""]\n2:T216d,not json at all\n3:{"kept":true}\n'
    assert list(payload.row_values(stream)) == [{"kept": True}]


def test_row_values_reads_a_row_that_follows_a_scalar_on_the_same_row():
    # Rows run together: a `null` row is followed by a real one on the next line.
    stream = '9:null\n5c:[1,2,3]\n'
    assert list(payload.row_values(stream)) == [None, [1, 2, 3]]


def test_find_matches_on_shape_not_position():
    soup = _page('1:{"other":1}\n', '2:{"id":7,"name":"x"}\n')
    assert payload.find(soup, "id", "name") == {"id": 7, "name": "x"}


def test_find_reaches_objects_nested_inside_a_row():
    soup = _page('1:["$","div",{"state":{"data":{"id":7,"name":"x"}}}]\n')
    assert payload.find(soup, "id", "name") == {"id": 7, "name": "x"}


def test_find_returns_none_when_no_object_matches():
    assert payload.find(_page('1:{"id":7}\n'), "id", "name") is None


def test_find_returns_none_without_a_payload():
    soup = BeautifulSoup("<html><body>plain page</body></html>", "html.parser")
    assert payload.find(soup, "id") is None
