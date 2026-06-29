"""Guardrail: /api/recommend must not call Open-Meteo when CACHE_ONLY_FORECASTS=true."""
import os
os.environ.setdefault("CACHE_ONLY_FORECASTS", "true")

from unittest.mock import patch
import pytest

import app as application


@pytest.fixture()
def _inject_site(monkeypatch):
    """Set CACHE_ONLY_FORECASTS=true and inject one site so recommend returns 200."""
    monkeypatch.setattr(application, "CACHE_ONLY_FORECASTS", True)
    with application._state["lock"]:
        original_sites = list(application._state["sites"])
        application._state["sites"] = [
            {
                "slug": "test-dark-sky",
                "name": "Test Dark Sky Site",
                "latitude": 51.5,
                "longitude": -0.12,
                "light_pollution": "low",
                "has_parking": False,
                "has_toilets": False,
            }
        ]
    yield
    with application._state["lock"]:
        application._state["sites"] = original_sites


def test_recommend_never_calls_openmeteo_in_cache_only_mode(_inject_site):
    """weather.get_cloud_cover_forecast must not be called when CACHE_ONLY_FORECASTS=true."""
    with patch("geocoder.geocode", return_value=(51.5, -0.1, "London, England")):
        with patch("weather.get_cloud_cover_forecast") as mock_gccf:
            client = application.app.test_client()
            resp = client.post(
                "/api/recommend",
                json={"location": "London", "max_distance_km": 200},
                content_type="application/json",
            )
    assert resp.status_code == 200
    assert mock_gccf.call_count == 0, (
        "weather.get_cloud_cover_forecast must not be called when CACHE_ONLY_FORECASTS=true"
    )
