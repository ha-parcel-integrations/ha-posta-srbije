"""Sample Pošta Srbije tracking replies shared by the test modules.

Shaped like a real reply to an exported parcel, with the numbers made up (they
carry a valid S10 check digit) and the foreign delivery office's postcode
blanked. ``StrOut`` is already parsed, as the API client hands it on.
"""

from __future__ import annotations

ACTIVE_CODE = "LX000000014RS"
DELIVERED_CODE = "RR000000028RS"
OTHER_CODE = "LX000000031RS"


def event(
    code: str,
    timestamp: str | None,
    status: str,
    place: str,
    *,
    number: str = ACTIVE_CODE,
    retur: str = "N",
    grams: int | None = None,
) -> dict:
    """One scan of the carrier's event list."""
    return {
        "Status": status,
        "StatusSifra": code,
        "ApiStatusSifra": code,
        "DatumKretanja": timestamp,
        "Datum": "",
        "Mesto": place,
        "MestoDo": "",
        "Faza": "",
        "Retur": retur,
        "Privremen": "",
        "Konacno": False,
        "Potpisnik": "",
        "PrijemniBroj": number,
        "Masa": grams,
        "ImaOtkupninu": False,
        "VrstaTransakcije": None,
        "IdCpmDan": None,
        "IdPosiljka": None,
        "BrojPosiljkeOTK": None,
        "BrojPosiljkePDK": None,
    }


def response(events_newest_first: list[dict]) -> dict:
    """The service's reply for a found item."""
    return {
        "Rezultat": 0,
        "StrOut": {
            "OtkupniDokumenti": None,
            "BrojPosiljkeOTK": None,
            "BrojPosiljkePDK": None,
            "Kretanja": events_newest_first,
        },
        "StrRezultat": "",
    }


def _scans(code: str) -> list[dict]:
    """Export parcel scans, newest first, ending in delivery."""
    return [
        event("UR", "2026-10-12T10:09:00", "Delivered", "GB-XXXX XXX", number=code),
        event("OT", "2026-10-10T20:51:00", "Dispatch", "GB-GBLALA", number=code),
        event("PS", "2026-10-10T17:50:00", "Arrived", "GB-GBLALA", number=code),
        event(
            "OT",
            "2026-10-01T10:07:34",
            "Transport",
            "11003 IZMENIČNA POŠTA",
            number=code,
        ),
        event(
            "OT",
            "2026-09-30T14:40:11",
            "Dispatch",
            "20250 POŠTA CARINJENJA",
            number=code,
        ),
        event(
            "PR",
            "2026-09-29T15:09:27",
            "Receipt",
            "21107 NOVI SAD",
            number=code,
            grams=104,
        ),
    ]


def delivered_sample(code: str = DELIVERED_CODE) -> dict:
    """A delivered export parcel with the whole history."""
    return response(_scans(code))


def active_sample(code: str = ACTIVE_CODE) -> dict:
    """The same parcel still in transit abroad."""
    return response(_scans(code)[1:])


def registered_sample(code: str = ACTIVE_CODE) -> dict:
    """The parcel right after acceptance."""
    return response(_scans(code)[-1:])


def pickup_sample(code: str = ACTIVE_CODE) -> dict:
    """A parcel the post office holds for the addressee."""
    scans = _scans(code)[1:]
    notified = event(
        "IZ", "2026-10-11T09:00:00", "Notified", "11000 BEOGRAD 6", number=code
    )
    return response([notified, *scans])
