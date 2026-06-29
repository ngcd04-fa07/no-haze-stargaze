"""Unit tests for geocoder.py — all HTTP calls are mocked."""
import pytest
import requests as req
from unittest.mock import MagicMock, patch

import geocoder


@pytest.fixture(autouse=True)
def clear_cache():
    geocoder._geocode_cache.clear()
    yield
    geocoder._geocode_cache.clear()


# ─── helpers ──────────────────────────────────────────────────────────────────

def _nom_ok(lat="51.7520", lon="-1.2577", name="Oxford, England"):
    m = MagicMock()
    m.raise_for_status.return_value = None
    m.json.return_value = [{"lat": lat, "lon": lon, "display_name": name}]
    return m


def _pc_ok(lat=51.5074, lon=-0.1278, postcode="SW1A 1AA"):
    m = MagicMock()
    m.raise_for_status.return_value = None
    m.json.return_value = {
        "status": 200,
        "result": {"latitude": lat, "longitude": lon, "postcode": postcode},
    }
    return m


# ─── cache ────────────────────────────────────────────────────────────────────

def test_cache_serves_second_call_without_http():
    with patch("requests.get", return_value=_nom_ok()) as m:
        r1 = geocoder.geocode("Oxford")
        r2 = geocoder.geocode("Oxford")
    assert r1 == r2
    assert m.call_count == 1


def test_cache_key_is_case_insensitive():
    with patch("requests.get", return_value=_nom_ok()) as m:
        r1 = geocoder.geocode("oxford")
        r2 = geocoder.geocode("OXFORD")
    assert r1 == r2
    assert m.call_count == 1


def test_failed_lookup_not_cached_so_retry_can_succeed():
    with patch("requests.get", side_effect=[req.Timeout(), _nom_ok()]) as m:
        r1 = geocoder.geocode("Oxford")
        r2 = geocoder.geocode("Oxford")
    assert r1 is None
    assert r2 is not None
    assert m.call_count == 2


# ─── postcode path ────────────────────────────────────────────────────────────

def test_postcode_primary_is_postcodes_io():
    with patch("requests.get", return_value=_pc_ok()) as m:
        result = geocoder.geocode("SW1A 1AA")
    assert result is not None
    first_url = m.call_args_list[0][0][0]
    assert "postcodes.io" in first_url


def test_postcode_falls_back_to_nominatim_when_postcodes_io_fails():
    with patch("requests.get", side_effect=[req.Timeout(), _nom_ok()]) as m:
        result = geocoder.geocode("SW1A 1AA")
    assert result is not None
    assert m.call_count == 2
    second_url = m.call_args_list[1][0][0]
    assert "nominatim" in second_url


def test_postcode_retries_postcodes_io_when_nominatim_also_fails():
    with patch("requests.get", side_effect=[req.Timeout(), req.Timeout(), _pc_ok()]) as m:
        result = geocoder.geocode("SW1A 1AA")
    assert result is not None
    assert m.call_count == 3
    third_url = m.call_args_list[2][0][0]
    assert "postcodes.io" in third_url


def test_postcode_returns_none_when_all_paths_fail():
    with patch("requests.get", side_effect=[req.Timeout(), req.Timeout(), req.Timeout()]):
        result = geocoder.geocode("SW1A 1AA")
    assert result is None


def test_postcodes_io_timeout_is_5s():
    with patch("requests.get", return_value=_pc_ok()) as m:
        geocoder.geocode("SW1A 1AA")
    _, kwargs = m.call_args_list[0]
    assert kwargs.get("timeout") == 5


# ─── nominatim path ───────────────────────────────────────────────────────────

def test_place_name_uses_nominatim():
    with patch("requests.get", return_value=_nom_ok()) as m:
        geocoder.geocode("Oxford")
    url = m.call_args[0][0]
    assert "nominatim" in url


def test_place_name_returns_none_when_nominatim_fails():
    with patch("requests.get", side_effect=req.Timeout()):
        result = geocoder.geocode("Oxford")
    assert result is None


def test_no_postcodes_io_fallback_for_plain_place_name():
    with patch("requests.get", side_effect=req.Timeout()) as m:
        geocoder.geocode("Oxford")
    assert m.call_count == 1


def test_nominatim_user_agent_is_set():
    with patch("requests.get", return_value=_nom_ok()) as m:
        geocoder.geocode("Oxford")
    _, kwargs = m.call_args
    ua = kwargs.get("headers", {}).get("User-Agent", "")
    assert ua != ""


def test_nominatim_timeout_is_5s():
    with patch("requests.get", return_value=_nom_ok()) as m:
        geocoder.geocode("Oxford")
    _, kwargs = m.call_args
    assert kwargs.get("timeout") == 5
