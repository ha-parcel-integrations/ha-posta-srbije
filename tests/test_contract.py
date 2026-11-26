"""The parcel contract's checks, run over every normalised sample."""

from datetime import datetime

import pytest

from custom_components.posta_srbije.const import ParcelStatus
from custom_components.posta_srbije.parcels import (
    BELGRADE,
    normalize_parcel,
    parse_iso,
    planned_for_status,
    planned_window,
)

from .payloads import (
    ACTIVE_CODE,
    active_sample,
    delivered_sample,
    event,
    pickup_sample,
    registered_sample,
    response,
)

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "contents",
    "status",
    "raw_status",
    "delivered",
    "delivered_at",
    "planned_from",
    "planned_to",
    "pickup",
    "pickup_point",
    "url",
    "weight",
    "dimensions",
    "history",
    "raw",
]

PLACE = "21107 NOVI SAD"


def _scans(*pairs: tuple[str, str], retur: str = "N") -> dict:
    """A reply whose scans are ``(code, timestamp)`` pairs, oldest first."""
    scans = [event(code, stamp, "x", PLACE, retur=retur) for code, stamp in pairs]
    return response(scans[::-1])


def _collected() -> dict:
    return _scans(
        ("PR", "2026-10-01T10:00:00"),
        ("IZ", "2026-10-11T09:00:00"),
        ("UR", "2026-10-12T16:00:00"),
    )


def _returned() -> dict:
    scans = [
        event("PR", "2026-10-01T10:00:00", "x", PLACE),
        event("UR", "2026-10-05T10:00:00", "x", PLACE, retur="D"),
    ]
    return response(scans[::-1])


def _returning() -> dict:
    return _scans(("PR", "2026-10-01T10:00:00"), ("UO", "2026-10-05T10:00:00"))


SAMPLES = {
    "delivered": delivered_sample,
    "active": active_sample,
    "registered": registered_sample,
    "at_pickup_point": pickup_sample,
    "collected": _collected,
    "returned": _returned,
    "returning": _returning,
    "problem": lambda: _scans(
        ("PR", "2026-10-01T10:00:00"), ("VR", "2026-10-02T10:00:00")
    ),
    "bare": dict,
}


@pytest.fixture(params=SAMPLES)
def parcel(request) -> dict:
    return normalize_parcel(SAMPLES[request.param](), tracking_code=ACTIVE_CODE)


def test_keys_and_order(parcel):
    assert list(parcel) == CANONICAL_KEYS


def test_delivered_rule(parcel):
    assert parcel["delivered"] is (parcel["status"] is ParcelStatus.DELIVERED)


def test_delivered_at_only_when_delivered(parcel):
    if not parcel["delivered"]:
        assert parcel["delivered_at"] is None


def test_planned_window_is_ordered_and_anchored(parcel):
    if parcel["planned_to"] is not None:
        assert parcel["planned_from"] is not None
        assert parse_iso(parcel["planned_to"]) > parse_iso(parcel["planned_from"])


def test_planned_is_empty_when_the_parcel_is_done_or_waiting(parcel):
    if parcel["status"] in (
        ParcelStatus.DELIVERED,
        ParcelStatus.AT_PICKUP_POINT,
        ParcelStatus.RETURNING,
    ):
        assert parcel["planned_from"] is None
        assert parcel["planned_to"] is None


def test_pickup_is_a_bool_and_the_point_needs_it(parcel):
    assert isinstance(parcel["pickup"], bool)
    if not parcel["pickup"]:
        assert parcel["pickup_point"] is None


def test_a_collected_pickup_parcel_stays_pickup():
    parcel = normalize_parcel(_collected(), tracking_code=ACTIVE_CODE)
    assert parcel["status"] is ParcelStatus.DELIVERED
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] == "2026-10-12T16:00:00+02:00"
    assert parcel["pickup"] is True


def test_a_parcel_never_at_a_pickup_point_is_not_pickup():
    parcel = normalize_parcel(delivered_sample(), tracking_code=ACTIVE_CODE)
    assert parcel["pickup"] is False


def test_a_return_leg_delivery_is_not_delivered_until_a_real_return_confirms_it():
    parcel = normalize_parcel(_returned(), tracking_code=ACTIVE_CODE)
    assert parcel["status"] is ParcelStatus.RETURNING
    assert parcel["delivered"] is False
    assert parcel["delivered_at"] is None


def test_a_return_still_travelling_is_not_delivered():
    parcel = normalize_parcel(_returning(), tracking_code=ACTIVE_CODE)
    assert parcel["delivered"] is False
    assert parcel["delivered_at"] is None


def test_a_moment_ends_at_the_end_of_that_day():
    planned_from, planned_to = planned_window(
        "2026-04-29T13:00:00Z", None, zone=BELGRADE
    )
    end = datetime.fromisoformat(planned_to)
    assert (end.hour, end.minute, end.second, end.microsecond) == (23, 59, 59, 0)
    assert end.date() == parse_iso(planned_from).date()


def test_a_date_covers_the_whole_day():
    planned_from, planned_to = planned_window("2026-04-29", None, zone=BELGRADE)
    start = datetime.fromisoformat(planned_from)
    end = datetime.fromisoformat(planned_to)
    assert (start.hour, start.minute, start.second) == (0, 0, 0)
    assert (end.hour, end.minute, end.second, end.microsecond) == (23, 59, 59, 0)
    assert start.date() == end.date()


def test_planned_for_status_empties_the_window():
    window = ("2026-04-29T00:00:00+02:00", "2026-04-29T23:59:59+02:00")
    for status in (
        ParcelStatus.DELIVERED,
        ParcelStatus.AT_PICKUP_POINT,
        ParcelStatus.RETURNING,
    ):
        assert planned_for_status(status, *window) == (None, None)
    assert planned_for_status(ParcelStatus.IN_TRANSIT, *window) == window
