"""Integration tests — real HTTP calls to Nominatim and postcodes.io.

Not run in CI (internet required). Run manually:
    pytest tests/test_geocoder_integration.py -v -s -m integration
"""
import time
import pytest

import geocoder

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def clear_cache():
    geocoder._geocode_cache.clear()
    yield
    geocoder._geocode_cache.clear()


def test_nominatim_real_response_time_and_result():
    start = time.monotonic()
    result = geocoder._geocode_nominatim("Oxford, England")
    elapsed = time.monotonic() - start

    print(f"\n  Nominatim response time: {elapsed:.3f}s")
    assert result is not None, "Nominatim returned no result for 'Oxford, England'"
    lat, lon, name = result
    assert 51.5 < lat < 52.0, f"Unexpected latitude for Oxford: {lat}"
    assert -1.6 < lon < -1.1, f"Unexpected longitude for Oxford: {lon}"
    assert elapsed < 5.0, f"Nominatim took {elapsed:.3f}s — would exceed 5s timeout"


def test_postcodes_io_real_response_time_and_result():
    start = time.monotonic()
    result = geocoder._geocode_postcode("SW1A 1AA")
    elapsed = time.monotonic() - start

    print(f"\n  postcodes.io response time: {elapsed:.3f}s")
    assert result is not None, "postcodes.io returned no result for SW1A 1AA"
    lat, lon, display = result
    assert 51.4 < lat < 51.6, f"Unexpected latitude for SW1A 1AA: {lat}"
    assert -0.2 < lon < -0.1, f"Unexpected longitude for SW1A 1AA: {lon}"
    assert elapsed < 5.0, f"postcodes.io took {elapsed:.3f}s — would exceed 5s timeout"
