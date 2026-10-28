"""Tests for body measurements via the v2 /measurements API."""

import json
from datetime import date

import pytest

from myfitnesspal_mcp import server
from test_diary_v2 import FakeClient, FakeResponse, FakeSession

TYPES = FakeResponse(
    200,
    {"items": [{"id": "1", "description": "Neck"}, {"id": "2", "description": "Waist"}, {"id": "3", "description": "Hips"}]},
)


class RoutingSession(FakeSession):
    """FakeSession that answers GETs by URL suffix and records every call."""

    def __init__(self, gets, post=None, delete=None):
        super().__init__(post=post, delete=delete)
        self._gets = gets

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return next(resp for suffix, resp in self._gets.items() if url.endswith(suffix))


def test_measurement_types_put_weight_first():
    session = RoutingSession({"/types": TYPES})
    assert server.get_measurement_types(FakeClient(session)) == ["Weight", "Neck", "Waist", "Hips"]


def test_resolve_matches_case_insensitively_without_writing():
    session = RoutingSession({"/types": TYPES})
    assert server.resolve_measurement_type(FakeClient(session), "waist") == "Waist"
    assert server.resolve_measurement_type(FakeClient(session), "WEIGHT") == "Weight"
    assert all(call[0] == "GET" for call in session.calls)


def test_resolve_missing_type_refuses_and_lists_types():
    session = RoutingSession({"/types": TYPES})
    with pytest.raises(RuntimeError, match="No 'chest'.*Weight, Neck, Waist, Hips.*create_type"):
        server.resolve_measurement_type(FakeClient(session), "chest")


def test_resolve_creates_missing_type_as_bare_name_list():
    session = RoutingSession({"/types": TYPES}, post=FakeResponse(201, [103104883100845]))
    assert server.resolve_measurement_type(FakeClient(session), "right thigh", create=True) == "Right Thigh"
    method, url, kwargs = session.calls[-1]
    assert (method, url) == ("POST", "https://api.myfitnesspal.com/v2/measurements/types")
    assert json.loads(kwargs["data"]) == {"items": ["Right Thigh"]}


def posted(session):
    return json.loads(session.calls[-1][2]["data"])["items"][0]


def test_set_weight_sends_api_unit():
    session = FakeSession(post=FakeResponse(200, {"items": [{"id": "9", "type": "Weight"}]}))
    item = server.set_measurement_v2(FakeClient(session), "Weight", 81.5, date(2026, 10, 28), "kg")
    assert item["id"] == "9"
    assert posted(session) == {"type": "Weight", "value": 81.5, "date": "2026-10-28", "unit": "kilograms"}


def test_set_weight_without_unit_refuses():
    session = FakeSession(post=FakeResponse(200, {"items": [{"id": "9"}]}))
    with pytest.raises(RuntimeError, match="'kg' or 'lb'"):
        server.set_measurement_v2(FakeClient(session), "Weight", 180, date(2026, 10, 28))
    assert session.calls == []


def test_set_body_measurement_sends_no_unit():
    session = FakeSession(post=FakeResponse(200, {"items": [{"id": "9", "type": "Waist"}]}))
    server.set_measurement_v2(FakeClient(session), "Waist", 84, date(2026, 10, 28), "cm")
    assert posted(session) == {"type": "Waist", "value": 84.0, "date": "2026-10-28"}


def test_set_measurement_treats_empty_items_as_failure():
    # MFP answers 200 with no items when it silently drops an entry.
    session = FakeSession(post=FakeResponse(200, {"items": []}))
    with pytest.raises(RuntimeError, match="did not store"):
        server.set_measurement_v2(FakeClient(session), "Chest", 100, date(2026, 10, 28))


def test_get_measurements_filters_dates_and_sorts_oldest_first():
    session = FakeSession(
        get=FakeResponse(
            200,
            {
                "items": [
                    {"id": 3, "type": "Weight", "value": 179.0, "date": "2026-10-25", "unit": "pounds"},
                    {"id": 1, "type": "Weight", "value": 182.0, "date": "2026-09-01", "unit": "pounds"},
                    {"id": 2, "type": "Weight", "value": 180.0, "date": "2026-10-18", "unit": "pounds"},
                ],
                "has_more": False,
            },
        )
    )
    result = server.get_measurements_v2(FakeClient(session), "Weight", date(2026, 10, 1), date(2026, 10, 28))
    assert [(e["id"], e["date"]) for e in result["entries"]] == [("2", "2026-10-18"), ("3", "2026-10-25")]
    assert session.calls[0][2]["params"] == {"types": "Weight"}


def test_delete_measurement_hits_v2_endpoint():
    session = FakeSession(delete=FakeResponse(204))
    server.delete_measurement_v2(FakeClient(session), "158346739928421")
    assert session.calls[0][:2] == ("DELETE", "https://api.myfitnesspal.com/v2/measurements/158346739928421")


def test_delete_measurement_surfaces_failure():
    session = FakeSession(delete=FakeResponse(404))
    with pytest.raises(RuntimeError, match="HTTP 404"):
        server.delete_measurement_v2(FakeClient(session), "1")
