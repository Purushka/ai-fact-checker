"""容错 JSON 解析。"""
from __future__ import annotations

from factcheck.utils.json_parse import parse_json_lenient


def test_pure_json():
    assert parse_json_lenient('{"a": 1}') == {"a": 1}


def test_json_in_markdown_fence():
    text = """```json
{"verdict": "supported", "confidence": 87}
```"""
    r = parse_json_lenient(text)
    assert r == {"verdict": "supported", "confidence": 87}


def test_json_with_leading_text():
    text = 'Here is the result: {"a": 1, "b": [2, 3]}'
    assert parse_json_lenient(text) == {"a": 1, "b": [2, 3]}


def test_empty_returns_none():
    assert parse_json_lenient("") is None
    assert parse_json_lenient(None) is None


def test_invalid_returns_none():
    assert parse_json_lenient("not a json at all") is None
