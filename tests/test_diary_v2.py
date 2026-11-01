"""Tests for the v2 diary add/delete port and the GET-based food search."""

import json
from datetime import date

import lxml.html
import pytest

from myfitnesspal_mcp import server


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    def __init__(self, get=None, post=None, delete=None):
        self.calls = []
        self._get, self._post, self._delete = get, post, delete

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self._get

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self._post

    def delete(self, url, **kwargs):
        self.calls.append(("DELETE", url, kwargs))
        return self._delete


class FakeClient:
    BASE_URL_SECURE = "https://www.myfitnesspal.com/"
    access_token = "token-123"
    user_id = "42"

    def __init__(self, session=None):
        self.session = session
        self.fetched_urls = []

    def _get_document_for_url(self, url):
        self.fetched_urls.append(url)
        return lxml.html.document_fromstring("<html><body></body></html>")

    def _get_food_search_results(self, document):
        return ["parsed"]


FOOD = {
    "id": "30946886598069",
    "version": "5102795690",
    "serving_sizes": [
        {"value": 1.0, "unit": "box", "nutrition_multiplier": 1.0, "id": "x"},
        {"value": 100.0, "unit": "g", "nutrition_multiplier": 0.4, "id": "y"},
    ],
}


def test_food_search_url_puts_query_in_url():
    url = server.food_search_url(FakeClient(), "starbucks caffe latte")
    assert url == (
        "https://www.myfitnesspal.com/food/search?search=starbucks%20caffe%20latte&page=1"
    )


def test_search_foods_fetches_results_page_with_get():
    client = FakeClient()
    assert server.search_foods(client, "banana") == ["parsed"]
    assert client.fetched_urls == [server.food_search_url(client, "banana")]


def test_select_serving_size_defaults_to_first_and_trims_fields():
    assert server.select_serving_size(FOOD) == {
        "value": 1.0,
        "unit": "box",
        "nutrition_multiplier": 1.0,
    }


def test_select_serving_size_matches_unit():
    assert server.select_serving_size(FOOD, "G")["unit"] == "g"


def test_select_serving_size_requires_servings():
    with pytest.raises(RuntimeError):
        server.select_serving_size({"id": "1", "serving_sizes": []})


def test_add_food_to_diary_posts_v2_entry_and_returns_id():
    session = FakeSession(
        get=FakeResponse(200, {"items": [FOOD]}),
        post=FakeResponse(201, {"items": [{"id": "uuid-1"}]}),
    )
    entry_id = server.add_food_to_diary(
        FakeClient(session), "30946886598069", "lunch", date(2026, 10, 28), 1.5
    )

    assert entry_id == "uuid-1"
    method, url, kwargs = session.calls[0]
    assert (method, url, kwargs["params"]) == (
        "GET",
        "https://api.myfitnesspal.com/v2/foods",
        {"ids": "30946886598069"},
    )
    method, url, kwargs = session.calls[1]
    assert (method, url) == ("POST", "https://api.myfitnesspal.com/v2/diary")
    assert kwargs["headers"]["Authorization"] == "Bearer token-123"
    assert json.loads(kwargs["data"]) == {
        "items": [
            {
                "type": "food_entry",
                "date": "2026-10-28",
                "meal_name": "Lunch",
                "servings": 1.5,
                "food": {"id": "30946886598069", "version": "5102795690"},
                "serving_size": {"value": 1.0, "unit": "box", "nutrition_multiplier": 1.0},
            }
        ]
    }


def test_add_food_to_diary_rejects_unknown_meal():
    session = FakeSession(get=FakeResponse(200, {"items": [FOOD]}))
    with pytest.raises(RuntimeError, match="Invalid meal"):
        server.add_food_to_diary(FakeClient(session), "1", "Brunch", date(2026, 10, 28))


def test_add_food_to_diary_surfaces_api_error():
    session = FakeSession(
        get=FakeResponse(200, {"items": [FOOD]}),
        post=FakeResponse(400, {"error_details": {"item_error": "version mismatch"}}),
    )
    with pytest.raises(RuntimeError, match="HTTP 400 - version mismatch"):
        server.add_food_to_diary(FakeClient(session), "1", "Lunch", date(2026, 10, 28))


def test_delete_food_entry_routes_v2_uuid_to_v2_api():
    session = FakeSession(delete=FakeResponse(204))
    server.delete_food_entry(FakeClient(session), "0b7c-uuid", date(2026, 10, 28))
    assert session.calls == [
        (
            "DELETE",
            "https://api.myfitnesspal.com/v2/diary/0b7c-uuid",
            {"headers": server._mfp_api_headers(FakeClient()), "timeout": 30},
        )
    ]


def _legacy(meal, name, quantity):
    return {"meal": meal, "name": name, "quantity": quantity}


def _v2(uuid, meal, description, servings=1.0, value=1.0):
    return {
        "id": uuid,
        "type": "food_entry",
        "meal_name": meal,
        "servings": servings,
        "serving_size": {"value": value},
        "food": {"description": description},
    }


LEGACY_DAY = {
    "101": _legacy("Lunch", "Thai Delivery - Cashew Chicken with Brown Rice, 1 box", "1"),
    "201": _legacy("Snacks", 'Apple, 1 medium (3" dia)', "1"),
    "202": _legacy("Snacks", "Evil Snacks - Mushroom Chips, 2 oz", "2"),
    "203": _legacy("Snacks", 'Apple, 1 medium (3" dia)', "1"),
}
V2_DAY = [
    _v2("u-lunch", "Lunch", "Thai Delivery - Cashew Chicken with Brown Rice"),
    _v2("u-apple-1", "Snacks", "Apple"),
    _v2("u-chips", "Snacks", "Evil Snacks - Mushroom Chips", servings=2.0),
    _v2("u-apple-2", "Snacks", "Apple"),
]


def test_find_food_entry_uuid_matches_by_meal_and_position():
    assert server.find_food_entry_uuid(LEGACY_DAY, "101", V2_DAY) == "u-lunch"
    assert server.find_food_entry_uuid(LEGACY_DAY, "202", V2_DAY) == "u-chips"
    # Two identical apples: the second legacy entry maps to the second v2 entry.
    assert server.find_food_entry_uuid(LEGACY_DAY, "203", V2_DAY) == "u-apple-2"


def test_find_food_entry_uuid_rejects_name_mismatch():
    swapped = [V2_DAY[0], V2_DAY[2], V2_DAY[1], V2_DAY[3]]
    with pytest.raises(RuntimeError, match="Could not match"):
        server.find_food_entry_uuid(LEGACY_DAY, "201", swapped)


def test_find_food_entry_uuid_rejects_quantity_mismatch():
    v2 = [V2_DAY[0], V2_DAY[1], _v2("u-chips", "Snacks", "Evil Snacks - Mushroom Chips"), V2_DAY[3]]
    with pytest.raises(RuntimeError, match="quantity"):
        server.find_food_entry_uuid(LEGACY_DAY, "202", v2)


def test_find_food_entry_uuid_rejects_count_mismatch():
    with pytest.raises(RuntimeError, match="entries"):
        server.find_food_entry_uuid(LEGACY_DAY, "201", V2_DAY[:3])


def test_delete_food_entry_routes_legacy_id_through_v2(monkeypatch):
    snapshots = [LEGACY_DAY, {k: v for k, v in LEGACY_DAY.items() if k != "203"}]
    monkeypatch.setattr(server, "get_diary_entry_snapshots", lambda client, d: snapshots.pop(0))
    monkeypatch.setattr(server, "get_food_entries_v2", lambda client, d: V2_DAY)
    session = FakeSession(delete=FakeResponse(204))

    deleted = server.delete_food_entry(FakeClient(session), "203", date(2026, 10, 30))

    assert deleted["name"].startswith("Apple")
    assert [(m, u) for m, u, _ in session.calls] == [
        ("DELETE", "https://api.myfitnesspal.com/v2/diary/u-apple-2")
    ]


def test_delete_food_entry_fails_if_entry_survives(monkeypatch):
    monkeypatch.setattr(server, "get_diary_entry_snapshots", lambda client, d: LEGACY_DAY)
    monkeypatch.setattr(server, "get_food_entries_v2", lambda client, d: V2_DAY)
    session = FakeSession(delete=FakeResponse(204))
    with pytest.raises(RuntimeError, match="still exists"):
        server.delete_food_entry(FakeClient(session), "203", date(2026, 10, 30))
