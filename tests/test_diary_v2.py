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
