"""Canonical parcel shape, status mapping and list helpers.

Everything in this module is a **pure function** — no I/O, no Home Assistant
objects beyond the config entry's options. That is deliberate: it keeps the
carrier-specific mapping (which you rewrite per carrier) apart from the
coordinator (which is nearly identical everywhere), and it makes the mapping
trivially unit-testable without spinning up HA.

Two things here are carrier-specific: :data:`_STATUS_MAP` and
:func:`normalize_parcel`. Everything else — the timestamp parsing, the history
builder, the sort contract, the delivered filter, the one-shot warning for
unmapped statuses — is suite-wide machinery and should be left alone.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
import re
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    HISTORY_MAX_EVENTS,
    ParcelStatus,
)

_LOGGER = logging.getLogger(__name__)

# Where users report a status we do not map yet. Rewritten by the bootstrap
# script; it must point at the carrier's own repo so the log line is
# copy-pasteable straight into a new issue.
#
# The ``?template=`` parameter matters: without it the link opens a blank form,
# and the report comes back missing the version and the log line we need.
NEW_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-posta-srbije/issues/new"
    "?template=unrecognised_status.yml"
)

# Keyed on the scan code. No out-for-delivery code is known, so none is mapped;
# a delivery back to the sender reads ``UR`` and is told apart by ``Retur``
# (see :func:`_effective_code`).
_STATUS_MAP: dict[str, ParcelStatus] = {
    "PR": ParcelStatus.REGISTERED,
    "OT": ParcelStatus.IN_TRANSIT,
    "PS": ParcelStatus.IN_TRANSIT,
    "IZ": ParcelStatus.AT_PICKUP_POINT,
    "ZI": ParcelStatus.AT_PICKUP_POINT,
    "VR": ParcelStatus.PROBLEM,
    "UR": ParcelStatus.DELIVERED,
    "UO": ParcelStatus.RETURNING,
}

# In the map on third-party evidence only: no real parcel has carried one yet.
# They map as well as we can guess and also report themselves once, so the
# first real one tells us whether the guess holds. ``UO`` is also what a
# delivery on a return leg maps through.
_UNCONFIRMED_CODES = frozenset({"IZ", "ZI", "VR", "UO"})

# One-shot warnings already logged this HA session, keyed by what they are
# about (a status code, a key set, a failure kind), so none repeats per poll.
_unmapped_statuses_logged: set[str] = set()


def warn_once(key: str, message: str, *args: Any) -> None:
    """Log ``message`` at WARNING the first time ``key`` is seen."""
    if key in _unmapped_statuses_logged:
        return
    _unmapped_statuses_logged.add(key)
    _LOGGER.warning(message, *args)


def _warn_unmapped_status(code: str) -> None:
    """Log an unmapped carrier status once, with a copy-paste issue link."""
    warn_once(
        f"status:{code}",
        "Unrecognised Pošta Srbije status code — help us map it. Open an "
        "issue and paste this line: %s\n  code=%s",
        NEW_ISSUE_URL,
        code,
    )


def map_event_status(code: str | None) -> ParcelStatus | None:
    """Map a history entry's status code to a canonical status, or ``None``.

    Unmapped codes keep ``status: null`` on the history entry (rather than
    ``unknown``, so a consumer can tell "no mapping" from "mapped to unknown")
    and warn once, reusing the parcel-status one-shot set.
    """
    if not code:
        return None
    mapped = _STATUS_MAP.get(code)
    if mapped is not None:
        return mapped
    _warn_unmapped_status(code)
    return None


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string to an aware datetime, or ``None`` on failure.

    Naive values are treated as UTC so a list always sorts without crashing on
    a mixed set.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def to_iso_timestamp(value: Any) -> str | None:
    """Return an ISO 8601 string for an API timestamp field.

    Numbers are treated as **epoch milliseconds** — the common case for the
    consumer APIs in this suite. Strings pass through untouched; their
    consumers are guarded by :func:`parse_iso`. Adjust the numeric branch if
    your carrier stamps in seconds.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return str(value)


def format_dimensions(
    length: float | None, width: float | None, height: float | None
) -> dict[str, Any] | None:
    """Return the canonical ``dimensions`` dict, or ``None`` when incomplete.

    Units contract: **centimetres**, with ``text`` pre-formatted as
    ``"L x W x H cm"`` (integer values, lowercase ``x``) so dashboards can show
    a dimension without doing their own formatting. Convert before calling if
    the carrier reports millimetres or inches.
    """
    if length is None or width is None or height is None:
        return None
    return {
        "length": length,
        "width": width,
        "height": height,
        "text": f"{round(length)} x {round(width)} x {round(height)} cm",
    }


def build_history(
    events: list | None,
    *,
    max_events: int = HISTORY_MAX_EVENTS,
    keep_order: bool = False,
) -> list[dict]:
    """Build the canonical ``history`` list from the carrier's event list.

    Each entry is ``{timestamp, status, raw_status}`` — identical across all
    suite carriers, and top-level (not under ``raw``) so it survives the
    aggregator's ``strip_raw()``. ``raw_status`` is the carrier's own text, or
    its event code when the API has no human-readable text. Sorted oldest →
    newest and capped to the most recent ``max_events``. ``keep_order`` trusts
    the caller's order instead, for carriers whose timestamps carry no zone.
    """
    parseable: list[tuple[datetime, dict]] = []
    unparseable: list[dict] = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        timestamp = to_iso_timestamp(event.get("timestamp"))
        if not timestamp:
            continue
        entry = {
            "timestamp": timestamp,
            "status": map_event_status(event.get("statusCode")),
            "raw_status": event.get("description") or event.get("statusCode"),
        }
        if keep_order:
            unparseable.append(entry)
            continue
        parsed = parse_iso(timestamp)
        if parsed is None:
            unparseable.append(entry)
        else:
            parseable.append((parsed, entry))
    parseable.sort(key=lambda item: item[0])
    ordered = [entry for _, entry in parseable] + unparseable
    return ordered[-max_events:]


BELGRADE = ZoneInfo("Europe/Belgrade")

# Foreign posts stamp scans on their own wall clock. Only countries with a
# single zone are listed; any other country's events keep the clock as given.
_COUNTRY_ZONES: dict[str, ZoneInfo] = {
    code: ZoneInfo(name)
    for code, name in {
        "AT": "Europe/Vienna",
        "BA": "Europe/Sarajevo",
        "BE": "Europe/Brussels",
        "BG": "Europe/Sofia",
        "CH": "Europe/Zurich",
        "CN": "Asia/Shanghai",
        "CZ": "Europe/Prague",
        "DE": "Europe/Berlin",
        "DK": "Europe/Copenhagen",
        "ES": "Europe/Madrid",
        "FI": "Europe/Helsinki",
        "FR": "Europe/Paris",
        "GB": "Europe/London",
        "GR": "Europe/Athens",
        "HR": "Europe/Zagreb",
        "HU": "Europe/Budapest",
        "IE": "Europe/Dublin",
        "IT": "Europe/Rome",
        "JP": "Asia/Tokyo",
        "ME": "Europe/Podgorica",
        "MK": "Europe/Skopje",
        "NL": "Europe/Amsterdam",
        "NO": "Europe/Oslo",
        "PL": "Europe/Warsaw",
        "PT": "Europe/Lisbon",
        "RO": "Europe/Bucharest",
        "SE": "Europe/Stockholm",
        "SI": "Europe/Ljubljana",
        "SK": "Europe/Bratislava",
        "TR": "Europe/Istanbul",
    }.items()
}

_SERBIAN_OFFICE = re.compile(r"^\d{5} ")
_FOREIGN_OFFICE = re.compile(r"^([A-Z]{2})-")


def _place_shape(place: Any) -> str:
    """Mask a place to its shape (digits ``9``, letters ``A``/``a``), never its value."""
    if not isinstance(place, str) or not place:
        return "none"
    return "".join(
        "9" if ch.isdigit() else ("A" if ch.isupper() else "a") if ch.isalpha() else ch
        for ch in place[:40]
    )


def _event_timestamp(event: dict) -> str | None:
    """Return the scan's ISO time, offset-aware when its zone is known.

    ``DatumKretanja`` has no offset: a Serbian office (``NNNNN NAME``) is on
    Europe/Belgrade and a foreign post (``CC-office``) on that country's clock.
    """
    value = event.get("DatumKretanja")
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.isoformat()
    place = event.get("Mesto")
    if not isinstance(place, str):
        place = ""
    zone: ZoneInfo | None = None
    if _SERBIAN_OFFICE.match(place):
        zone = BELGRADE
    elif foreign := _FOREIGN_OFFICE.match(place):
        zone = _COUNTRY_ZONES.get(foreign.group(1))
    if zone is None:
        if not _SERBIAN_OFFICE.match(place) and not _FOREIGN_OFFICE.match(place):
            shape = _place_shape(event.get("Mesto"))
            warn_once(
                f"place:{shape}",
                "Unrecognised Pošta Srbije place form — scan times there are "
                "kept without a time zone. Open an issue and paste this "
                "line: %s\n  shape=%s",
                NEW_ISSUE_URL,
                shape,
            )
        return parsed.isoformat()
    return parsed.replace(tzinfo=zone).isoformat()


def _warn_unconfirmed(code: str, *, derived: bool) -> None:
    """Report a mapped code that no real parcel has carried yet, once."""
    key = "UR-return" if derived else code
    warn_once(
        f"unconfirmed:{key}",
        "Pošta Srbije status code %s has never been seen on a real parcel "
        "here and was read as '%s'. If it looks wrong, open an issue and "
        "paste this line: %s",
        "UR on a return leg" if derived else code,
        _STATUS_MAP[code].value,
        NEW_ISSUE_URL,
    )


def _raw_parts(event: dict) -> list[str]:
    """Return the ``;``-separated parts of the event's status code."""
    raw = event.get("ApiStatusSifra") or event.get("StatusSifra")
    if not isinstance(raw, str):
        return []
    return [part.strip() for part in raw.split(";") if part.strip()]


def _effective_code(event: dict) -> str | None:
    """Return the status code to map for one scan.

    A code may hold ``;``-separated parts; the first mapped part wins. A ``UR``
    on a return leg (``Retur == "D"``) is a delivery back to the sender, which
    the carrier words as "Delivered", so it maps through ``UO``.
    """
    parts = _raw_parts(event)
    if not parts:
        return None
    code = next((part for part in parts if part in _STATUS_MAP), parts[0])
    if code == "UR" and event.get("Retur") == "D":
        return "UO"
    return code


def _events_oldest_first(raw: dict) -> list[dict]:
    """Return the scans oldest first, with identical repeats dropped."""
    out = raw.get("StrOut")
    events = out.get("Kretanja") if isinstance(out, dict) else None
    ordered: list[dict] = []
    for event in reversed(events if isinstance(events, list) else []):
        if isinstance(event, dict) and event not in ordered:
            ordered.append(event)
    return ordered


def _weight_kg(events: list[dict]) -> float | None:
    """Return the weight in kg; grams arrive on the acceptance scan only."""
    for event in events:
        if _effective_code(event) != "PR":
            continue
        grams = event.get("Masa")
        if (
            isinstance(grams, (int, float))
            and not isinstance(grams, bool)
            and grams > 0
        ):
            return grams / 1000
    return None


def normalize_parcel(
    raw: dict, *, tracking_code: str, include_history: bool = False
) -> dict:
    """Return a carrier-agnostic parcel dict with the payload under ``raw``.

    ``raw`` is the service's reply with ``StrOut`` parsed (an empty dict for a
    number with no history yet); ``tracking_code`` is the number the user
    entered. The **keys of the returned dict are the contract** shared by every
    carrier in the suite.
    """
    events = _events_oldest_first(raw)
    scans: list[dict] = []
    for event in events:
        code = _effective_code(event)
        if code in _UNCONFIRMED_CODES:
            _warn_unconfirmed(code, derived=code not in _raw_parts(event))
        timestamp = _event_timestamp(event)
        if timestamp is None:
            warn_once(
                "timestamp",
                "Unreadable Pošta Srbije scan time — help us map it. Open an "
                "issue and paste this line: %s\n  key=DatumKretanja",
                NEW_ISSUE_URL,
            )
        scans.append(
            {
                "timestamp": timestamp,
                "statusCode": code,
                "description": event.get("Status"),
            }
        )

    status = ParcelStatus.UNKNOWN
    for scan in reversed(scans):
        code = scan["statusCode"]
        if code in _STATUS_MAP:
            if status is ParcelStatus.UNKNOWN:
                status = _STATUS_MAP[code]
        elif code:
            _warn_unmapped_status(code)
    delivered = status is ParcelStatus.DELIVERED
    newest = scans[-1] if scans else None

    delivered_at = None
    if delivered:
        delivered_at = next(
            (
                scan["timestamp"]
                for scan in reversed(scans)
                if scan["statusCode"] == "UR"
            ),
            None,
        )

    return {
        "carrier": "Pošta Srbije",
        "barcode": tracking_code,
        "sender": None,
        "receiver": None,
        "status": status,
        "raw_status": newest["description"] if newest else None,
        "delivered": delivered,
        "delivered_at": delivered_at,
        "planned_from": None,
        "planned_to": None,
        "pickup": status is ParcelStatus.AT_PICKUP_POINT,
        "pickup_point": None,
        "url": None,
        "weight": _weight_kg(events),
        "dimensions": None,
        "history": (build_history(scans, keep_order=True) if include_history else None),
        "raw": raw,
    }


def sort_parcels_by_ts(
    parcels: list[dict], key_field: str, *, descending: bool = False
) -> list[dict]:
    """Return normalised parcels sorted by the ISO timestamp at ``key_field``.

    The suite's sort contract: incoming/outgoing ascending on ``planned_from``,
    delivered descending on ``delivered_at``. Parcels whose value is missing or
    unparseable always sort to the end, regardless of ``descending``.
    """
    with_ts: list[tuple[datetime, dict]] = []
    without_ts: list[dict] = []
    for parcel in parcels:
        parsed = parse_iso(parcel.get(key_field))
        if parsed is None:
            without_ts.append(parcel)
        else:
            with_ts.append((parsed, parcel))
    with_ts.sort(key=lambda item: item[0], reverse=descending)
    return [parcel for _, parcel in with_ts] + without_ts


def apply_delivered_filter(parcels: list[dict], entry: ConfigEntry) -> list[dict]:
    """Trim the delivered list per the entry's retention option.

    ``parcels`` must already be sorted newest-first. ``days`` keeps deliveries
    from the last N days (an unparseable ``delivered_at`` is kept rather than
    silently dropped); the ``parcels`` type keeps the N most recent. Parcels
    stay *tracked* either way — this only controls what the delivered sensor
    shows.
    """
    options = entry.options
    filter_type = options.get(CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE)
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(UTC) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]
