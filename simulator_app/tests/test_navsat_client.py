"""Unit tests for simulator_app.services.navsat_client using respx to mock HTTP.

No real NavSat token/network needed — every response is mocked.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from simulator_app.services.navsat_client import NavSatClient, NavSatClientError, NavSatRecord

URL = "https://navsat.test/laststate"


def _raw(plate: str = "SJB1234", estado: str = "movimiento") -> dict:
    return {
        "plateNumber": plate,
        "deviceName": "device-1",
        "crDateTime": "2026-08-03 10:00:00",
        "latitude": 9.9365,
        "longitude": -84.0511,
        "odometer": 1000,
        "speed": 20,
        "lugar": "San Jose",
        "estado": estado,
    }


# ---------------------------------------------------------------------------
# NavSatRecord.from_api
# ---------------------------------------------------------------------------


def test_from_api_keeps_estado_verbatim() -> None:
    record = NavSatRecord.from_api(_raw(estado="detenido"))
    assert record.plate_number == "SJB1234"
    assert record.latitude == 9.9365
    assert record.longitude == -84.0511
    assert record.estado == "detenido"


def test_from_api_missing_estado_defaults_to_empty_string() -> None:
    raw = _raw()
    del raw["estado"]
    record = NavSatRecord.from_api(raw)
    assert record.estado == ""


def test_from_api_missing_required_field_raises_keyerror() -> None:
    raw = _raw()
    del raw["plateNumber"]
    with pytest.raises(KeyError):
        NavSatRecord.from_api(raw)


# ---------------------------------------------------------------------------
# NavSatClient.fetch — happy path
# ---------------------------------------------------------------------------


@respx.mock
async def test_fetch_returns_parsed_records() -> None:
    respx.get(URL).mock(
        return_value=httpx.Response(
            200, json=[_raw("A111", "movimiento"), _raw("B222", "detenido")]
        )
    )
    client = NavSatClient(url=URL)
    try:
        records = await client.fetch()
    finally:
        await client.close()

    assert [r.plate_number for r in records] == ["A111", "B222"]
    assert [r.estado for r in records] == ["movimiento", "detenido"]


@respx.mock
async def test_fetch_skips_malformed_records_but_keeps_valid_ones() -> None:
    respx.get(URL).mock(
        return_value=httpx.Response(200, json=[_raw("GOOD1"), {"not": "valid"}, "not-a-dict"])
    )
    client = NavSatClient(url=URL)
    try:
        records = await client.fetch()
    finally:
        await client.close()

    assert [r.plate_number for r in records] == ["GOOD1"]


# ---------------------------------------------------------------------------
# NavSatClient.fetch — error paths
# ---------------------------------------------------------------------------


@respx.mock
async def test_fetch_raises_on_http_error() -> None:
    respx.get(URL).mock(return_value=httpx.Response(500))
    client = NavSatClient(url=URL)
    try:
        with pytest.raises(NavSatClientError):
            await client.fetch()
    finally:
        await client.close()


@respx.mock
async def test_fetch_raises_on_non_json_body() -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, text="<html>not json</html>"))
    client = NavSatClient(url=URL)
    try:
        with pytest.raises(NavSatClientError):
            await client.fetch()
    finally:
        await client.close()


@respx.mock
async def test_fetch_raises_when_response_is_not_a_list() -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, json={"unexpected": "object"}))
    client = NavSatClient(url=URL)
    try:
        with pytest.raises(NavSatClientError):
            await client.fetch()
    finally:
        await client.close()
