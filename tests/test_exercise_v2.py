"""Tests for exercise search, logging and deletion via the v2 diary API."""

import json
from datetime import date

import pytest

from myfitnesspal_mcp import server
from test_diary_v2 import FakeClient, FakeResponse, FakeSession

DAY = date(2026, 10, 28)

CATALOG = [
    {"id": "133476463340909", "name": "Decline Bench Press"},
    {"id": "134026227510637", "name": "Bench (Chest) Press, Machine"},
    {"id": "134028370832877", "name": "Bench Press, Barbell"},
    {"id": "134026252660589", "name": "Squat"},
    {"id": "134026256887789", "name": "Machine Squat"},
    {"id": "19387100491453", "name": "Kickboxing"},
]


def lookup_page(exercises):
    data = {
        "props": {
            "pageProps": {
                "dehydratedState": {
                    "queries": [
                        {"queryKey": ["other"], "state": {"data": None}},
                        {"queryKey": ["allExercises"], "state": {"data": {"exercises": exercises}}},
                    ]
                }
            }
        }
    }
    return (
        '<html><script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(data)
        + "</script></html>"
    )


def test_parse_exercise_catalog_reads_next_data():
    page = lookup_page([{"id": 134028370832877, "description": "Bench Press, Barbell"}])
    assert server.parse_exercise_catalog(page) == [
        {"id": "134028370832877", "name": "Bench Press, Barbell"}
    ]


def test_parse_exercise_catalog_without_data_raises():
    with pytest.raises(RuntimeError):
        server.parse_exercise_catalog("<html></html>")


def test_search_ranks_exact_then_all_words():
    names = [e["name"] for e in server.search_exercise_catalog(CATALOG, "bench press")]
    assert names[:2] == ["Bench Press, Barbell", "Bench (Chest) Press, Machine"]
    assert "Kickboxing" not in names


def test_search_matches_plural_by_prefix():
    names = [e["name"] for e in server.search_exercise_catalog(CATALOG, "squats")]
    assert names == ["Squat", "Machine Squat"]


def post_session():
    return FakeSession(
        post=FakeResponse(
            201,
            {
                "items": [
                    {
                        "id": "uuid-1",
                        "exercise": {"description": "Bench Press, Barbell", "type": "strength"},
                        "sets": 3,
                        "reps_per_set": 8,
                        "weight_per_set": {"unit": "pounds", "value": 132.28},
                    }
                ]
            },
        )
    )


def posted_item(session):
    method, url, kwargs = session.calls[0]
    assert (method, url) == ("POST", "https://api.myfitnesspal.com/v2/diary")
    return json.loads(kwargs["data"])["items"][0]


def test_add_strength_posts_sets_reps_and_weight_in_kg():
    session = post_session()
    item = server.add_exercise_to_diary(
        FakeClient(session), "134028370832877", DAY, sets=3, reps=8, weight=60
    )
    assert item["id"] == "uuid-1"
    assert posted_item(session) == {
        "type": "exercise_entry",
        "date": "2026-10-28",
        "exercise": {"id": "134028370832877"},
        "sets": 3,
        "reps_per_set": 8,
        "weight_per_set": {"unit": "kilograms", "value": 60.0},
    }


def test_add_bodyweight_strength_sends_zero_weight_in_pounds():
    session = post_session()
    server.add_exercise_to_diary(FakeClient(session), "1", DAY, sets=3, reps=10, weight_unit="lbs")
    assert posted_item(session)["weight_per_set"] == {"unit": "pounds", "value": 0.0}


def test_add_cardio_posts_duration_seconds_and_calories():
    session = post_session()
    server.add_exercise_to_diary(FakeClient(session), "19387100491453", DAY, minutes=45, calories=520)
    item = posted_item(session)
    assert item["duration"] == 2700
    assert item["energy"] == {"unit": "calories", "value": 520.0}
    assert item["start_time"] == "2026-10-28T12:00:00+00:00"
    assert "sets" not in item


def test_add_cardio_passes_given_start_time():
    session = post_session()
    server.add_exercise_to_diary(
        FakeClient(session), "1", DAY, minutes=60, calories=700, start_time="2026-10-28T18:30:00+07:00"
    )
    assert posted_item(session)["start_time"] == "2026-10-28T18:30:00+07:00"


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({}, "either sets and reps"),
        ({"sets": 3, "reps": 8, "minutes": 10, "calories": 50}, "either sets and reps"),
        ({"sets": 3}, "both sets and reps"),
        ({"minutes": 30}, "both minutes and calories"),
        ({"sets": 3, "reps": 8, "weight": 10, "weight_unit": "stone"}, "Unknown weight unit"),
    ],
)
def test_add_exercise_rejects_bad_arguments(kwargs, message):
    session = post_session()
    with pytest.raises(RuntimeError, match=message):
        server.add_exercise_to_diary(FakeClient(session), "1", DAY, **kwargs)
    assert session.calls == []


def test_add_exercise_surfaces_api_error():
    session = FakeSession(
        post=FakeResponse(400, {"error_details": {"item_error": "Quantity ^Please enter a valid number of minutes"}})
    )
    with pytest.raises(RuntimeError, match="HTTP 400 - Quantity"):
        server.add_exercise_to_diary(FakeClient(session), "1", DAY, sets=1, reps=1)


DIARY_ITEMS = {
    "items": [
        {"id": "adj", "type": "exercise_entry", "is_calorie_adjustment": True,
         "exercise": {"description": "MFP iOS calorie adjustment", "type": "cardio"}},
        {"id": "bench-1", "type": "exercise_entry", "sets": 3, "reps_per_set": 8,
         "weight_per_set": {"unit": "pounds", "value": 132.28},
         "exercise": {"description": "Bench Press, Barbell", "type": "strength"}},
        {"id": "squat-1", "type": "exercise_entry", "sets": 5, "reps_per_set": 5,
         "weight_per_set": {"unit": "pounds", "value": 220.46},
         "exercise": {"description": "Squat", "type": "strength"}},
        {"id": "squat-2", "type": "exercise_entry", "sets": 1, "reps_per_set": 3,
         "weight_per_set": {"unit": "pounds", "value": 242.5},
         "exercise": {"description": "Machine Squat", "type": "strength"}},
    ]
}


def test_get_exercise_entries_skips_calorie_adjustments():
    session = FakeSession(get=FakeResponse(200, DIARY_ITEMS))
    entries = server.get_exercise_entries_v2(FakeClient(session), DAY)
    assert [e["id"] for e in entries] == ["bench-1", "squat-1", "squat-2"]
    assert session.calls[0][2]["params"] == {"entry_date": "2026-10-28", "types": "exercise_entry"}


def test_delete_by_name_removes_the_single_match():
    session = FakeSession(get=FakeResponse(200, DIARY_ITEMS), delete=FakeResponse(204))
    deleted = server.delete_exercise_entry(FakeClient(session), DAY, name="bench")
    assert deleted["entry_id"] == "bench-1"
    assert session.calls[-1][:2] == ("DELETE", "https://api.myfitnesspal.com/v2/diary/bench-1")


def test_delete_by_ambiguous_name_lists_entries_and_deletes_nothing():
    session = FakeSession(get=FakeResponse(200, DIARY_ITEMS), delete=FakeResponse(204))
    with pytest.raises(RuntimeError, match="More than one.*squat-1.*squat-2"):
        server.delete_exercise_entry(FakeClient(session), DAY, name="squat")
    assert all(call[0] != "DELETE" for call in session.calls)


def test_summarize_cardio_entry_reports_minutes():
    summary = server.summarize_exercise_entry(
        {"id": "k", "duration": 2700, "energy": {"unit": "calories", "value": 520.0},
         "exercise": {"description": "Kickboxing", "type": "cardio"}}
    )
    assert summary == {"entry_id": "k", "name": "Kickboxing", "type": "cardio", "minutes": 45.0, "calories": 520.0}
