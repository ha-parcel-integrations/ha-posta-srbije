"""Tests for the Pošta Srbije API client."""

import json
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.posta_srbije import api as api_module
from custom_components.posta_srbije.api import (
    MAX_BODY_BYTES,
    PostaSrbijeApiClient,
    PostaSrbijeApiError,
)

from .payloads import ACTIVE_CODE, OTHER_CODE, active_sample, event, response

NOT_FOUND_EN = (
    "Postal item not found. Please check the accuracy of the number you entered."
)
NOT_FOUND_SR = "Pošiljka nije pronađena. Proverite ispravnost unetog broja."


def _wire(reply: dict) -> dict:
    """The reply as sent: ``StrOut`` and ``StrRezultat`` are JSON text."""
    wire = dict(reply)
    if isinstance(wire.get("StrOut"), dict):
        wire["StrOut"] = json.dumps(wire["StrOut"])
    return wire


def _not_found(text: str = NOT_FOUND_EN) -> dict:
    notice = json.dumps({"Poruka": text, "PorukaKorisnik": text, "Info": "x"})
    return {"Rezultat": 1, "StrOut": None, "StrRezultat": notice}


def _session(
    body: object,
    *,
    status: int = 200,
    headers: dict | None = None,
) -> MagicMock:
    if isinstance(body, bytes):
        raw = body
    elif isinstance(body, str):
        raw = body.encode()
    else:
        raw = json.dumps(body).encode()

    async def _chunks(_size):
        for start in range(0, len(raw), 1000):
            yield raw[start : start + 1000]

    resp = MagicMock()
    resp.status = status
    resp.headers = headers or {}
    resp.content.iter_chunked = _chunks
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.post = MagicMock(return_value=ctx)
    return session


async def _get(body: object, code: str = ACTIVE_CODE, **kwargs):
    return await PostaSrbijeApiClient(_session(body, **kwargs)).async_get_parcel(code)


async def test_found_reply_comes_back_with_str_out_parsed():
    parcel = await _get(_wire(active_sample()))

    assert parcel["Rezultat"] == 0
    assert isinstance(parcel["StrOut"], dict)
    assert len(parcel["StrOut"]["Kretanja"]) == 5


async def test_str_out_already_an_object_is_accepted():
    parcel = await _get(active_sample())
    assert parcel["StrOut"]["Kretanja"][0]["StatusSifra"] == "OT"


async def test_request_envelope():
    session = _session(_wire(active_sample()))
    client = PostaSrbijeApiClient(session)
    await client.async_get_parcel(ACTIVE_CODE)
    await client.async_get_parcel(ACTIVE_CODE)

    first, second = (json.loads(c.kwargs["data"]) for c in session.post.call_args_list)
    assert session.post.call_args.args[0] == "https://api.posta.rs/paketi"
    assert session.post.call_args.kwargs["headers"] == {
        "Accept": "application/json",
        "Content-Type": "application/json; charset=utf-8",
    }
    assert first["Servis"] == 111
    assert first["IdVrstaTranskacije"] == 63
    assert json.loads(first["StrIn"]) == {
        "VrstaUsluge": 1,
        "EksterniBroj": "",
        "PrijemniBroj": ACTIVE_CODE,
    }
    assert json.loads(first["StrKlijent"])["Jezik"] == "ENG"
    assert first["IdTransakcija"] != second["IdTransakcija"]


@pytest.mark.parametrize("text", [NOT_FOUND_EN, NOT_FOUND_SR])
async def test_not_found_notice_returns_none(text):
    assert await _get(_not_found(text)) is None


async def test_not_found_notice_without_user_message_field():
    reply = _not_found()
    reply["StrRezultat"] = json.dumps({"Poruka": NOT_FOUND_EN})
    assert await _get(reply) is None


async def test_not_found_notice_as_plain_text():
    reply = _not_found()
    reply["StrRezultat"] = NOT_FOUND_EN
    assert await _get(reply) is None


async def test_unrecognised_notice_is_an_error_and_warns_once(caplog):
    reply = _not_found("Something else entirely")
    for _ in range(2):
        with pytest.raises(PostaSrbijeApiError):
            await _get(reply)
    assert caplog.text.count("unrecognised notice") == 1
    assert "issues/new?template=unrecognised_status.yml" in caplog.text


@pytest.mark.parametrize("notice", [None, 5, {"x": 1}])
async def test_notice_of_an_odd_shape_is_an_error(notice):
    reply = _not_found()
    reply["StrRezultat"] = notice
    with pytest.raises(PostaSrbijeApiError):
        await _get(reply)


async def test_refused_request_is_an_error_without_detail(caplog):
    reply = {"Rezultat": 3, "StrOut": None, "StrRezultat": json.dumps({"Poruka": "no"})}
    with pytest.raises(PostaSrbijeApiError):
        await _get(reply)
    assert "refused" in caplog.text
    assert "issues/new" in caplog.text
    for constant in (api_module._CLIENT_ID, api_module._CLIENT_KEY):
        assert constant not in caplog.text


async def test_unseen_result_code_is_an_error_and_warns(caplog):
    with pytest.raises(PostaSrbijeApiError):
        await _get({"Rezultat": 7, "StrOut": None, "StrRezultat": ""})
    assert "Rezultat=7" in caplog.text


async def test_429_carries_retry_after():
    with pytest.raises(PostaSrbijeApiError) as err:
        await _get({}, status=429, headers={"Retry-After": "120"})
    assert err.value.status_code == 429
    assert err.value.retry_after == 120.0


async def test_429_with_a_date_has_no_retry_after():
    with pytest.raises(PostaSrbijeApiError) as err:
        await _get(
            {}, status=429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}
        )
    assert err.value.retry_after is None


async def test_429_without_header():
    with pytest.raises(PostaSrbijeApiError) as err:
        await _get({}, status=429)
    assert err.value.retry_after is None


async def test_server_error_carries_status():
    with pytest.raises(PostaSrbijeApiError) as err:
        await _get({}, status=503)
    assert err.value.status_code == 503


@pytest.mark.parametrize(
    "page",
    [
        "<html><body>x</body></html>",
        "Request Rejected",
        "Access Denied",
        "403 Forbidden",
    ],
)
async def test_block_page_is_an_error_and_warns_once(page, caplog):
    for _ in range(2):
        with pytest.raises(PostaSrbijeApiError):
            await _get(page)
    assert caplog.text.count("web page instead of data") == 1


async def test_unparseable_body_is_an_error():
    with pytest.raises(PostaSrbijeApiError):
        await _get("not json at all")


async def test_non_object_body_is_an_error():
    with pytest.raises(PostaSrbijeApiError):
        await _get(["a"])


async def test_oversized_body_is_refused():
    with pytest.raises(PostaSrbijeApiError, match="too large"):
        await _get(b"x" * (MAX_BODY_BYTES + 1))


async def test_reply_for_another_number_is_inconclusive(caplog):
    with pytest.raises(PostaSrbijeApiError):
        await _get(_wire(active_sample(OTHER_CODE)))
    assert "different number" in caplog.text
    assert OTHER_CODE not in caplog.text


async def test_missing_number_counts_as_a_mismatch():
    reply = response([event("OT", "2026-10-01T10:00:00", "Dispatch", "x", number="")])
    with pytest.raises(PostaSrbijeApiError):
        await _get(_wire(reply))


async def test_empty_history_is_unknown():
    assert await _get(_wire(response([]))) is None


@pytest.mark.parametrize("out", [None, "not json", "[1]", 5])
async def test_bad_str_out_is_an_error(out):
    reply = {"Rezultat": 0, "StrOut": out, "StrRezultat": ""}
    with pytest.raises(PostaSrbijeApiError):
        await _get(reply)


async def test_str_out_without_event_list_is_an_error():
    reply = {"Rezultat": 0, "StrOut": json.dumps({"Kretanja": "x"}), "StrRezultat": ""}
    with pytest.raises(PostaSrbijeApiError):
        await _get(reply)


async def test_non_object_event_is_an_error():
    reply = response([])
    reply["StrOut"]["Kretanja"] = ["x"]
    with pytest.raises(PostaSrbijeApiError):
        await _get(_wire(reply))


async def test_unseen_keys_warn_once_with_names_only(caplog):
    reply = _wire(active_sample())
    reply["Extra"] = "secret-value"
    out = json.loads(reply["StrOut"])
    out["OutExtra"] = 1
    out["Kretanja"][0]["EventExtra"] = "other-value"
    reply["StrOut"] = json.dumps(out)

    for _ in range(2):
        await _get(reply)

    assert "keys=Extra" in caplog.text
    assert "keys=OutExtra" in caplog.text
    assert "keys=EventExtra" in caplog.text
    assert caplog.text.count("keys=EventExtra") == 1
    assert "secret-value" not in caplog.text
    assert "other-value" not in caplog.text


async def test_known_keys_do_not_warn(caplog):
    await _get(_wire(active_sample()))
    assert "keys=" not in caplog.text


async def test_network_error_propagates():
    session = MagicMock()
    session.post = MagicMock(side_effect=aiohttp.ClientError("boom"))
    with pytest.raises(aiohttp.ClientError):
        await PostaSrbijeApiClient(session).async_get_parcel(ACTIVE_CODE)
