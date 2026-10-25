"""Pošta Srbije tracking client.

``async_get_parcel`` returns the full reply (``StrOut`` already parsed into an
object) for a known item, ``None`` when the service says the number is
unknown, and raises :class:`PostaSrbijeApiError` for everything else — with
``status_code`` set on a non-200 and ``retry_after`` on a 429 that carried
seconds. ``aiohttp.ClientError`` propagates untouched.

Every reply is HTTP 200, including not-found and a refused request, so the
outcome is decided on the body's ``Rezultat``, never on the status code.
"""

from __future__ import annotations

import json
import logging
from typing import Any
import uuid

import aiohttp

from .const import TRACKING_API_URL
from .parcels import NEW_ISSUE_URL, warn_once

_LOGGER = logging.getLogger(__name__)

MAX_BODY_BYTES = 512 * 1024
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20)

_CLIENT_ID = "MAPeN"
_CLIENT_KEY = "<09;znCF_£..%B{93K##"

RESULT_FOUND = 0
RESULT_NOT_FOUND = 1
RESULT_REFUSED = 3

_NOT_FOUND_NOTICES = frozenset(
    {
        "postal item not found. please check the accuracy of the number you entered.",
        "pošiljka nije pronađena. proverite ispravnost unetog broja.",
    }
)
_BLOCK_PAGE_MARKERS = ("<html", "request rejected", "access denied", "forbidden")

_KNOWN_TOP_KEYS = frozenset({"Rezultat", "StrOut", "StrRezultat"})
_KNOWN_OUT_KEYS = frozenset(
    {"BrojPosiljkeOTK", "BrojPosiljkePDK", "Kretanja", "OtkupniDokumenti"}
)
_KNOWN_EVENT_KEYS = frozenset(
    {
        "ApiStatusSifra",
        "BrojPosiljkeOTK",
        "BrojPosiljkePDK",
        "Datum",
        "DatumKretanja",
        "Faza",
        "IdCpmDan",
        "IdPosiljka",
        "ImaOtkupninu",
        "Konacno",
        "Masa",
        "Mesto",
        "MestoDo",
        "Potpisnik",
        "PrijemniBroj",
        "Privremen",
        "Retur",
        "Status",
        "StatusSifra",
        "VrstaTransakcije",
    }
)


class PostaSrbijeApiError(Exception):
    """Raised when a Pošta Srbije API call returns an unexpected response."""

    def __init__(
        self,
        detail: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        """Store the status code and the ``Retry-After`` header, if any."""
        super().__init__(f"Pošta Srbije API request failed: {detail}")
        self.detail = detail
        self.status_code = status_code
        self.retry_after = retry_after


def _warn_unknown_keys(scope: str, keys: set[str], known: frozenset[str]) -> None:
    """Warn once per set of unseen keys; key names only, never values."""
    unseen = sorted(str(key) for key in keys - known)
    if unseen:
        warn_once(
            f"keys:{scope}:{','.join(unseen)}",
            "Unrecognised Pošta Srbije %s keys — help us map them. Open an "
            "issue and paste this line: %s\n  keys=%s",
            scope,
            NEW_ISSUE_URL,
            ", ".join(unseen),
        )


def _notice(raw: Any) -> str:
    """Return the user-facing notice text of a ``StrRezultat`` value."""
    value: Any = raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except ValueError:
            return raw
    if isinstance(value, dict):
        value = value.get("PorukaKorisnik") or value.get("Poruka") or ""
    return value if isinstance(value, str) else ""


async def _read_capped(response: aiohttp.ClientResponse) -> bytes:
    """Read the body, refusing anything over :data:`MAX_BODY_BYTES`."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.content.iter_chunked(65536):
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise PostaSrbijeApiError("response too large")
        chunks.append(chunk)
    return b"".join(chunks)


class PostaSrbijeApiClient:
    """Client for the Pošta Srbije item-tracking service."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialise the client with an aiohttp session."""
        self._session = session

    @staticmethod
    def _request_body(tracking_code: str) -> str:
        return json.dumps(
            {
                "TipSerijalizacije": 2,
                "StrKlijent": json.dumps(
                    {
                        "Username": _CLIENT_ID,
                        "Password": _CLIENT_KEY,
                        "Jezik": "ENG",
                        "IdTipUredjaja": 3,
                    }
                ),
                "Servis": 111,
                "IdVrstaTranskacije": 63,
                "IdTransakcija": str(uuid.uuid4()),
                "StrIn": json.dumps(
                    {
                        "VrstaUsluge": 1,
                        "EksterniBroj": "",
                        "PrijemniBroj": tracking_code,
                    }
                ),
            }
        )

    async def async_get_parcel(self, tracking_code: str) -> dict[str, Any] | None:
        """Fetch one item's history, or ``None`` when the number is unknown."""
        async with self._session.post(
            TRACKING_API_URL,
            data=self._request_body(tracking_code),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json; charset=utf-8",
            },
            timeout=REQUEST_TIMEOUT,
        ) as response:
            if response.status == 429:
                retry_after_header = response.headers.get("Retry-After")
                try:
                    retry_after = (
                        float(retry_after_header) if retry_after_header else None
                    )
                except ValueError:
                    retry_after = None  # an HTTP-date, not seconds
                raise PostaSrbijeApiError(
                    "HTTP 429", status_code=429, retry_after=retry_after
                )
            if response.status != 200:
                raise PostaSrbijeApiError(
                    f"HTTP {response.status}", status_code=response.status
                )
            body = await _read_capped(response)

        text = body.decode("utf-8", errors="replace")
        try:
            payload = json.loads(text)
        except ValueError as err:
            lowered = text.lower()
            if any(marker in lowered for marker in _BLOCK_PAGE_MARKERS):
                warn_once(
                    "block-page",
                    "Pošta Srbije answered with a web page instead of data — "
                    "the service may be blocking requests. Open an issue and "
                    "paste this line: %s",
                    NEW_ISSUE_URL,
                )
                raise PostaSrbijeApiError("blocked by an edge page") from err
            raise PostaSrbijeApiError("unparseable body") from err

        if not isinstance(payload, dict):
            raise PostaSrbijeApiError("unexpected body (not a JSON object)")
        _warn_unknown_keys("response", set(payload), _KNOWN_TOP_KEYS)

        result = payload.get("Rezultat")
        if result == RESULT_NOT_FOUND:
            if _notice(payload.get("StrRezultat")).strip().lower() in (
                _NOT_FOUND_NOTICES
            ):
                return None
            warn_once(
                "notice:1",
                "Pošta Srbije answered with an unrecognised notice. Open an "
                "issue and paste this line: %s\n  Rezultat=1",
                NEW_ISSUE_URL,
            )
            raise PostaSrbijeApiError("unrecognised notice")
        if result == RESULT_REFUSED:
            warn_once(
                "refused",
                "Pošta Srbije refused the tracking request. Open an issue "
                "and paste this line: %s",
                NEW_ISSUE_URL,
            )
            raise PostaSrbijeApiError("request refused")
        if result != RESULT_FOUND:
            warn_once(
                f"rezultat:{result}",
                "Pošta Srbije answered with an unseen result code. Open an "
                "issue and paste this line: %s\n  Rezultat=%s",
                NEW_ISSUE_URL,
                result,
            )
            raise PostaSrbijeApiError("unexpected result code")

        return self._parse_found(payload, tracking_code)

    @staticmethod
    def _parse_found(
        payload: dict[str, Any], tracking_code: str
    ) -> dict[str, Any] | None:
        """Validate a ``Rezultat == 0`` reply and parse its ``StrOut``."""
        out = payload.get("StrOut")
        if isinstance(out, str):
            try:
                out = json.loads(out)
            except ValueError as err:
                raise PostaSrbijeApiError("unparseable StrOut") from err
        if not isinstance(out, dict):
            raise PostaSrbijeApiError("StrOut is not an object")
        _warn_unknown_keys("result", set(out), _KNOWN_OUT_KEYS)

        events = out.get("Kretanja")
        if not isinstance(events, list):
            raise PostaSrbijeApiError("StrOut has no event list")
        if not events:
            return None

        for event in events:
            if not isinstance(event, dict):
                raise PostaSrbijeApiError("event is not an object")
            _warn_unknown_keys("event", set(event), _KNOWN_EVENT_KEYS)
            number = str(event.get("PrijemniBroj") or "").strip().upper()
            if number != tracking_code:
                warn_once(
                    "number-mismatch",
                    "Pošta Srbije answered for a different number than "
                    "asked; the reply was ignored. Open an issue and paste "
                    "this line: %s",
                    NEW_ISSUE_URL,
                )
                raise PostaSrbijeApiError("reply is for a different number")

        return {**payload, "StrOut": out}
