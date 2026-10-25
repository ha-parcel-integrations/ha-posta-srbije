"""Tests for the pure parcel-mapping helpers.

These need no Home Assistant instance — the whole point of keeping
``parcels.py`` free of I/O is that the carrier-specific mapping (the part you
rewrite per carrier) can be tested as plain functions.
"""

from datetime import UTC, datetime, timedelta

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.posta_srbije.const import (
    CAPABILITIES,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DOMAIN,
    KNOWN_CAPABILITIES,
    PENDING_CAPABILITIES,
    ParcelStatus,
)
from custom_components.posta_srbije.parcels import (
    apply_delivered_filter,
    build_history,
    format_dimensions,
    map_event_status,
    normalize_parcel,
    parse_iso,
    sort_parcels_by_ts,
    to_iso_timestamp,
)

from .payloads import (
    ACTIVE_CODE,
    DELIVERED_CODE,
    active_sample,
    delivered_sample,
    event,
    pickup_sample,
    registered_sample,
    response,
)

# ---------------------------------------------------------------------------
# status map
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("PR", ParcelStatus.REGISTERED),
        ("OT", ParcelStatus.IN_TRANSIT),
        ("PS", ParcelStatus.IN_TRANSIT),
        ("IZ", ParcelStatus.AT_PICKUP_POINT),
        ("ZI", ParcelStatus.AT_PICKUP_POINT),
        ("VR", ParcelStatus.PROBLEM),
        ("UR", ParcelStatus.DELIVERED),
        ("UO", ParcelStatus.RETURNING),
    ],
)
def test_every_known_code_maps(code, expected):
    assert map_event_status(code) == expected


def _status_of(*codes: str, **extra) -> ParcelStatus:
    """Status of a parcel whose scans are ``codes``, oldest first."""
    scans = [
        event(code, f"2026-10-0{index + 1}T10:00:00", "x", "21107 NOVI SAD", **extra)
        for index, code in enumerate(codes)
    ]
    return normalize_parcel(response(list(reversed(scans))), tracking_code=ACTIVE_CODE)[
        "status"
    ]


def test_a_delivery_back_to_the_sender_is_returning_not_delivered():
    """The carrier words it "Delivered"; only Retur says it is a return."""
    scans = [
        event("PR", "2026-10-01T10:00:00", "Receipt", "21107 NOVI SAD"),
        event("UR", "2026-10-05T10:00:00", "Delivered", "21107 NOVI SAD", retur="D"),
    ]
    parcel = normalize_parcel(response(scans[::-1]), tracking_code=ACTIVE_CODE)
    assert parcel["status"] == ParcelStatus.RETURNING
    assert parcel["delivered"] is False
    assert parcel["delivered_at"] is None


def test_a_normal_delivery_with_retur_n_or_blank_is_delivered():
    for retur in ("N", ""):
        assert _status_of("PR", "UR", retur=retur) == ParcelStatus.DELIVERED


def test_the_newest_mapped_event_wins_over_older_ones():
    assert _status_of("PR", "OT", "PS") == ParcelStatus.IN_TRANSIT
    assert _status_of("PR", "OT", "IZ") == ParcelStatus.AT_PICKUP_POINT
    assert _status_of("OT", "IZ", "VR") == ParcelStatus.PROBLEM


def test_a_returned_parcel_is_not_pulled_back_by_an_older_delivery_scan():
    """Band ordering: the newest scan decides, not the 'highest' status."""
    assert _status_of("PR", "UR", "UO") == ParcelStatus.RETURNING


def test_a_multi_part_code_takes_the_first_mapped_part():
    assert _status_of("XX;OT") == ParcelStatus.IN_TRANSIT
    assert _status_of("OT;UR") == ParcelStatus.IN_TRANSIT


def test_api_status_code_wins_over_status_code():
    scan = event("PR", "2026-10-01T10:00:00", "x", "21107 NOVI SAD")
    scan["StatusSifra"] = "XX"
    assert (
        normalize_parcel(response([scan]), tracking_code=ACTIVE_CODE)["status"]
        == ParcelStatus.REGISTERED
    )
    del scan["ApiStatusSifra"]
    scan["StatusSifra"] = "OT"
    assert (
        normalize_parcel(response([scan]), tracking_code=ACTIVE_CODE)["status"]
        == ParcelStatus.IN_TRANSIT
    )


def test_unmapped_newest_code_falls_back_to_history_and_warns_once(caplog):
    scans = [
        event("OT", "2026-10-01T10:00:00", "Dispatch", "21107 NOVI SAD"),
        event("ZZ", "2026-10-02T10:00:00", "Mystery", "21107 NOVI SAD"),
    ]
    for _ in range(2):
        parcel = normalize_parcel(response(scans[::-1]), tracking_code=ACTIVE_CODE)
    assert parcel["status"] == ParcelStatus.IN_TRANSIT
    assert parcel["raw_status"] == "Mystery"
    assert caplog.text.count("code=ZZ") == 1
    assert "issues/new?template=unrecognised_status.yml" in caplog.text


def test_only_unmapped_codes_give_unknown_with_a_warning(caplog):
    scans = [event("ZZ", "2026-10-02T10:00:00", "Mystery", "21107 NOVI SAD")]
    parcel = normalize_parcel(response(scans), tracking_code=ACTIVE_CODE)
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert "code=ZZ" in caplog.text


def test_map_event_status_missing_and_unmapped_are_none():
    """History keeps ``null`` rather than ``unknown`` so consumers can tell
    "no mapping" from "mapped to unknown"."""
    assert map_event_status(None) is None
    assert map_event_status("SOMETHING_NEW") is None
    assert map_event_status("UR") == ParcelStatus.DELIVERED


def test_unmapped_status_warns_only_once(caplog):
    assert map_event_status("ABDUCTED") is None
    assert map_event_status("ABDUCTED") is None
    assert caplog.text.count("ABDUCTED") == 1
    assert "issues/new" in caplog.text


# ---------------------------------------------------------------------------
# timestamp helpers
# ---------------------------------------------------------------------------


def test_parse_iso_handles_z_naive_and_garbage():
    assert parse_iso("2026-04-29T13:12:42Z").tzinfo is not None
    # A naive value is assumed UTC so mixed lists still sort.
    assert parse_iso("2026-04-29T13:12:42").tzinfo == UTC
    assert parse_iso("not-a-date") is None
    assert parse_iso(None) is None


def test_to_iso_timestamp_converts_epoch_milliseconds():
    assert to_iso_timestamp(1784203767167) == "2026-07-16T12:09:27.167000+00:00"
    assert to_iso_timestamp("2026-04-29T13:12:42Z") == "2026-04-29T13:12:42Z"
    assert to_iso_timestamp(None) is None
    assert to_iso_timestamp(10**20) is None  # out of range -> None, never raises


def test_format_dimensions_needs_all_three_axes():
    assert format_dimensions(30, 20, 10) == {
        "length": 30,
        "width": 20,
        "height": 10,
        "text": "30 x 20 x 10 cm",
    }
    assert format_dimensions(30, None, 10) is None


# ---------------------------------------------------------------------------
# build_history
# ---------------------------------------------------------------------------


def _scan(code, timestamp, text):
    return {"statusCode": code, "timestamp": timestamp, "description": text}


def test_build_history_orders_oldest_to_newest():
    history = build_history(
        [
            _scan("UR", "2026-04-29T13:12:42Z", "Delivered"),
            _scan("PR", "2026-04-27T23:03:58Z", "Receipt"),
        ]
    )
    assert [entry["raw_status"] for entry in history] == ["Receipt", "Delivered"]
    assert history[0]["status"] == ParcelStatus.REGISTERED
    assert history[-1]["status"] == ParcelStatus.DELIVERED


def test_build_history_caps_to_max_events():
    events = [
        _scan("OT", f"2026-04-{day:02d}T10:00:00Z", "moved") for day in range(1, 26)
    ]
    assert len(build_history(events, max_events=20)) == 20


def test_build_history_handles_missing_and_malformed():
    assert build_history(None) == []
    assert build_history([{"statusCode": "OT"}]) == []  # no timestamp
    assert build_history(["not-a-dict"]) == []


def test_build_history_keeps_unparseable_timestamp_last():
    history = build_history(
        [
            _scan("PR", "2026-04-24T10:00:00Z", "fine"),
            _scan("OT", "not-a-date", "odd"),
        ]
    )
    assert [entry["raw_status"] for entry in history] == ["fine", "odd"]


def test_build_history_falls_back_to_status_code_without_text():
    history = build_history([_scan("OT", "2026-04-24T10:00:00Z", "")])
    assert history[0]["raw_status"] == "OT"


# ---------------------------------------------------------------------------
# normalize_parcel — the canonical contract
# ---------------------------------------------------------------------------

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
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


def _normalize(raw, **kwargs):
    return normalize_parcel(
        raw, tracking_code=kwargs.pop("code", ACTIVE_CODE), **kwargs
    )


def test_normalize_publishes_exactly_the_canonical_keys():
    """The aggregator and cross-carrier dashboards depend on this key set."""
    assert list(_normalize(delivered_sample())) == CANONICAL_KEYS


def test_capabilities_are_known_values():
    """A typo here would silently misreport this carrier on the docs site."""
    assert CAPABILITIES <= KNOWN_CAPABILITIES
    assert PENDING_CAPABILITIES <= KNOWN_CAPABILITIES


def test_a_capability_is_never_both_populated_and_pending():
    """The docs site would have to pick one; "awaiting data" must not hide a confirmed field."""
    assert not CAPABILITIES & PENDING_CAPABILITIES


def test_capabilities_match_what_normalize_parcel_actually_returns():
    """Every declared CAPABILITIES entry must come true somewhere in a sample;
    everything neither declared nor pending must stay ``None``."""
    delivered = _normalize(delivered_sample())
    active = _normalize(active_sample())
    pickup = _normalize(pickup_sample())
    with_history = _normalize(delivered_sample(), include_history=True)

    if "weight" in CAPABILITIES:
        assert delivered["weight"] is not None
    if "dimensions" in CAPABILITIES:
        assert delivered["dimensions"] is not None
    if "delivery_window" in CAPABILITIES:
        assert active["planned_from"] is not None or active["planned_to"] is not None
    if "pickup_point" in CAPABILITIES:
        assert pickup["pickup_point"] is not None
    if "history" in CAPABILITIES:
        assert with_history["history"] is not None

    # Not declared: always literal None.
    for field in ("dimensions", "pickup_point", "planned_from", "planned_to", "url"):
        assert delivered[field] is None
        assert active[field] is None
        assert pickup[field] is None
    assert "pickup_point" in PENDING_CAPABILITIES


def test_normalize_delivered_parcel():
    parcel = _normalize(delivered_sample(DELIVERED_CODE), code=DELIVERED_CODE)
    assert parcel["carrier"] == "Pošta Srbije"
    assert parcel["barcode"] == DELIVERED_CODE
    assert parcel["sender"] is None
    assert parcel["receiver"] is None
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["raw_status"] == "Delivered"
    assert parcel["delivered"] is True
    # Foreign delivery time on the UK clock: 10:09 BST is 09:09 UTC.
    assert parcel["delivered_at"] == "2026-10-12T10:09:00+01:00"
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None
    assert parcel["pickup"] is False
    assert parcel["pickup_point"] is None
    assert parcel["url"] is None
    assert parcel["weight"] == pytest.approx(0.104)
    assert parcel["dimensions"] is None
    assert parcel["history"] is None  # opt-in, default off


def test_normalize_history_is_opt_in_and_oldest_first():
    parcel = _normalize(delivered_sample(), include_history=True)
    history = parcel["history"]
    assert len(history) == 6
    assert history[0] == {
        "timestamp": "2026-09-29T15:09:27+02:00",
        "status": ParcelStatus.REGISTERED,
        "raw_status": "Receipt",
    }
    assert history[-1]["status"] == ParcelStatus.DELIVERED


def test_history_is_capped_to_the_newest_twenty():
    scans = [
        event("OT", f"2026-09-{day:02d}T10:00:00", "Dispatch", "21107 NOVI SAD")
        for day in range(30, 0, -1)
    ]
    history = _normalize(response(scans), include_history=True)["history"]
    assert len(history) == 20
    assert history[-1]["timestamp"].startswith("2026-09-30")


def test_serbian_office_times_are_belgrade_clock():
    parcel = _normalize(registered_sample(), include_history=True)
    assert parcel["history"][0]["timestamp"] == "2026-09-29T15:09:27+02:00"
    # After the autumn change the offset follows.
    scans = [event("OT", "2026-12-01T10:00:00", "x", "11003 IZMENIČNA POŠTA")]
    assert (
        _normalize(response(scans), include_history=True)["history"][0]["timestamp"]
        == "2026-12-01T10:00:00+01:00"
    )


def test_foreign_times_use_the_countrys_clock_or_stay_as_given():
    scans = [
        event("OT", "2026-10-10T20:51:00", "x", "GB-GBLALA"),
        event("OT", "2026-10-10T20:52:00", "x", "DE-DEFRAA"),
        event("OT", "2026-10-10T20:53:00", "x", "US-USJFKA"),
        event("OT", "2026-10-10T20:54:00", "x", "somewhere"),
    ]
    stamps = [
        entry["timestamp"]
        for entry in _normalize(response(scans[::-1]), include_history=True)["history"]
    ]
    assert stamps == [
        "2026-10-10T20:51:00+01:00",
        "2026-10-10T20:52:00+02:00",
        "2026-10-10T20:53:00",
        "2026-10-10T20:54:00",
    ]


def test_a_timestamp_with_its_own_offset_is_kept():
    scans = [event("OT", "2026-10-10T20:51:00+03:00", "x", "GB-GBLALA")]
    history = _normalize(response(scans), include_history=True)["history"]
    assert history[0]["timestamp"] == "2026-10-10T20:51:00+03:00"


def test_unreadable_scan_time_warns_once_and_keeps_the_status(caplog):
    scans = [event("UR", "garbage", "Delivered", "21107 NOVI SAD")]
    for _ in range(2):
        parcel = _normalize(response(scans), include_history=True)
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["delivered_at"] is None
    assert parcel["history"] == []
    assert caplog.text.count("DatumKretanja") == 1
    assert "garbage" not in caplog.text


def test_missing_scan_time_also_warns(caplog):
    scans = [event("OT", None, "Dispatch", "21107 NOVI SAD")]
    _normalize(response(scans))
    assert "DatumKretanja" in caplog.text


def test_duplicate_scans_are_dropped():
    scan = event("OT", "2026-10-01T10:00:00", "Dispatch", "21107 NOVI SAD")
    history = _normalize(response([scan, dict(scan)]), include_history=True)["history"]
    assert len(history) == 1


def test_non_dict_scans_are_ignored():
    raw = response([event("OT", "2026-10-01T10:00:00", "x", "21107 NOVI SAD")])
    raw["StrOut"]["Kretanja"].append("junk")
    assert _normalize(raw)["status"] == ParcelStatus.IN_TRANSIT


def test_delivered_at_is_the_newest_delivery_scan():
    scans = [
        event("UR", "2026-10-12T10:09:00", "Delivered", "21107 NOVI SAD"),
        event("UR", "2026-10-11T10:09:00", "Delivered", "21107 NOVI SAD"),
    ]
    assert _normalize(response(scans))["delivered_at"] == "2026-10-12T10:09:00+02:00"


def test_normalize_pickup_parcel():
    parcel = _normalize(pickup_sample())
    assert parcel["status"] == ParcelStatus.AT_PICKUP_POINT
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] is None


def test_normalize_pending_placeholder():
    """A tracked-but-not-yet-scanned code still yields a full parcel dict."""
    parcel = _normalize({}, code="LX000000031RS")
    assert parcel["barcode"] == "LX000000031RS"
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert parcel["delivered"] is False
    assert parcel["raw_status"] is None
    assert parcel["weight"] is None
    assert parcel["history"] is None


def test_normalize_keeps_the_full_raw_reply():
    raw = active_sample()
    assert _normalize(raw)["raw"] is raw


def test_raw_status_is_the_newest_scans_wording():
    assert _normalize(active_sample())["raw_status"] == "Dispatch"


def test_str_out_that_is_not_parsed_yields_an_empty_parcel():
    parcel = _normalize({"Rezultat": 0, "StrOut": "text"})
    assert parcel["status"] == ParcelStatus.UNKNOWN


@pytest.mark.parametrize("grams", [None, 0, -5, "104", True])
def test_weight_needs_a_positive_number(grams):
    scans = [
        event("PR", "2026-10-01T10:00:00", "Receipt", "21107 NOVI SAD", grams=grams)
    ]
    assert _normalize(response(scans))["weight"] is None


def test_weight_is_read_from_the_acceptance_scan_only():
    scans = [
        event("OT", "2026-10-02T10:00:00", "x", "21107 NOVI SAD", grams=999),
        event("PR", "2026-10-01T10:00:00", "Receipt", "21107 NOVI SAD", grams=2500),
    ]
    assert _normalize(response(scans))["weight"] == 2.5
    only_transit = scans[:1]
    assert _normalize(response(only_transit))["weight"] is None


def test_the_foreign_postcode_and_signer_never_reach_the_canonical_fields():
    scans = [
        event("UR", "2026-10-12T10:09:00", "Delivered", "GB-AB1 2CD"),
    ]
    scans[0]["Potpisnik"] = "Jane Doe"
    parcel = _normalize(response(scans), include_history=True)
    published = {k: v for k, v in parcel.items() if k != "raw"}
    assert "AB1" not in str(published)
    assert "Jane" not in str(published)


# ---------------------------------------------------------------------------
# sort_parcels_by_ts
# ---------------------------------------------------------------------------


def test_sort_parcels_ascending_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "planned_from": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "planned_from": None},
        {"barcode": "c", "planned_from": "2026-05-01T10:00:00Z"},
    ]
    ordered = [p["barcode"] for p in sort_parcels_by_ts(parcels, "planned_from")]
    assert ordered == ["c", "a", "b"]


def test_sort_parcels_descending_still_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "delivered_at": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "delivered_at": "nonsense"},
        {"barcode": "c", "delivered_at": "2026-05-01T10:00:00Z"},
    ]
    ordered = [
        p["barcode"]
        for p in sort_parcels_by_ts(parcels, "delivered_at", descending=True)
    ]
    assert ordered == ["a", "c", "b"]


# ---------------------------------------------------------------------------
# apply_delivered_filter
# ---------------------------------------------------------------------------


def _entry(filter_type: str, amount: int) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_DELIVERED_FILTER_TYPE: filter_type,
            CONF_DELIVERED_FILTER_AMOUNT: amount,
        },
        unique_id=DOMAIN,
    )


def _delivered_pair() -> list[dict]:
    now = datetime.now(UTC)
    return [
        {"barcode": "RECENT", "delivered_at": (now - timedelta(days=1)).isoformat()},
        {"barcode": "OLD", "delivered_at": (now - timedelta(days=30)).isoformat()},
    ]


def test_delivered_filter_by_days():
    kept = apply_delivered_filter(_delivered_pair(), _entry("days", 7))
    assert [p["barcode"] for p in kept] == ["RECENT"]


def test_delivered_filter_by_count():
    parcels = _delivered_pair()
    assert apply_delivered_filter(parcels, _entry("parcels", 1)) == parcels[:1]


def test_delivered_filter_keeps_unparseable_timestamp():
    """Better to show a parcel with a broken date than to silently drop it."""
    parcels = [{"barcode": "WEIRD", "delivered_at": "nonsense"}]
    assert apply_delivered_filter(parcels, _entry("days", 7)) == parcels


# ---------------------------------------------------------------------------
# odd scan codes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", [None, "", "  ", ";", " ; "])
def test_scans_without_a_usable_code_are_skipped_silently(code, caplog):
    scans = [event("PR", "2026-10-01T10:00:00", "Receipt", "21107 NOVI SAD")]
    odd = event("PR", "2026-10-02T10:00:00", "Odd", "21107 NOVI SAD")
    odd["ApiStatusSifra"] = code
    odd["StatusSifra"] = code
    parcel = _normalize(response([odd, *scans]))
    assert parcel["status"] == ParcelStatus.REGISTERED
    assert "Unrecognised" not in caplog.text


def test_a_non_text_place_keeps_the_clock_as_given():
    scan = event("OT", "2026-10-01T10:00:00", "x", "x")
    scan["Mesto"] = None
    history = _normalize(response([scan]), include_history=True)["history"]
    assert history[0]["timestamp"] == "2026-10-01T10:00:00"


# ---------------------------------------------------------------------------
# unconfirmed codes, odd places, history order
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("IZ", ParcelStatus.AT_PICKUP_POINT),
        ("ZI", ParcelStatus.AT_PICKUP_POINT),
        ("VR", ParcelStatus.PROBLEM),
        ("UO", ParcelStatus.RETURNING),
    ],
)
def test_mapped_but_unseen_codes_keep_their_mapping_and_warn_once(
    code, expected, caplog
):
    for _ in range(2):
        assert _status_of("OT", code) == expected
    assert caplog.text.count(f"status code {code} ") == 1
    assert "issues/new?template=unrecognised_status.yml" in caplog.text


def test_a_return_leg_delivery_warns_once_as_unconfirmed(caplog):
    scans = [
        event("UR", "2026-10-05T10:00:00", "Delivered", "21107 NOVI SAD", retur="D")
    ]
    for _ in range(2):
        assert (
            normalize_parcel(response(scans), tracking_code=ACTIVE_CODE)["status"]
            == ParcelStatus.RETURNING
        )
    assert caplog.text.count("UR on a return leg") == 1


def test_confirmed_codes_do_not_warn_as_unconfirmed(caplog):
    _normalize(delivered_sample())
    assert "never been seen" not in caplog.text


@pytest.mark.parametrize(
    ("place", "shape"),
    [
        ("Somewhere 12", "Aaaaaaaaa 99"),
        ("", "none"),
        (None, "none"),
        ("ŠABAC-1", "AAAAA-9"),
    ],
)
def test_unrecognised_place_form_warns_once_with_its_shape_only(place, shape, caplog):
    scan = event("OT", "2026-10-01T10:00:00", "x", "x")
    scan["Mesto"] = place
    for _ in range(2):
        history = _normalize(response([scan]), include_history=True)["history"]
    assert history[0]["timestamp"] == "2026-10-01T10:00:00"
    assert caplog.text.count("place form") == 1
    assert f"shape={shape}" in caplog.text
    if place:
        assert place not in caplog.text


def test_known_place_forms_do_not_warn(caplog):
    _normalize(delivered_sample())  # Serbian offices and GB- places
    _normalize(response([event("OT", "2026-10-01T10:00:00", "x", "US-USJFKA")]))
    assert "place form" not in caplog.text


def test_history_keeps_the_carriers_order_even_when_naive_times_disagree():
    """A naive US time read as UTC must not jump ahead of a later Belgrade scan."""
    scans = [
        event("OT", "2026-10-02T09:00:00", "later", "11003 IZMENIČNA POŠTA"),
        event("OT", "2026-10-02T08:30:00", "earlier", "US-USJFKA"),
    ]
    history = _normalize(response(scans), include_history=True)["history"]
    assert [entry["raw_status"] for entry in history] == ["earlier", "later"]
    assert _normalize(response(scans))["raw_status"] == "later"


def test_build_history_can_keep_the_callers_order():
    history = build_history(
        [
            _scan("OT", "2026-04-29T13:12:42Z", "second"),
            _scan("PR", "2026-04-27T23:03:58Z", "first"),
        ],
        keep_order=True,
    )
    assert [entry["raw_status"] for entry in history] == ["second", "first"]
