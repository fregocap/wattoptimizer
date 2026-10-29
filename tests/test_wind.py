import io
import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from wattoptimizer import Course, wind
from wattoptimizer.wind import GriddedWind, fetch_open_meteo, head_wind_component


def _fake_response(lats, wind_from):
    hours = [f"2026-07-12T{h:02d}:00" for h in range(24)]
    stations = [
        {
            "latitude": lat,
            "hourly": {
                "time": hours,
                "wind_speed_10m": [4.0] * 24,
                "wind_direction_10m": [wind_from] * 24,
                "temperature_2m": [18.0 + 0.5 * h for h in range(24)],
            },
        }
        for lat in lats
    ]
    return io.BytesIO(json.dumps(stations if len(stations) > 1 else stations[0]).encode())


def test_fetch_open_meteo_parses_multi_station_response(monkeypatch):
    x = np.linspace(0.0, 20_000.0, 201)
    course = Course.from_profile(x, np.zeros_like(x), 90.0)  # riding east
    requested = {}

    def fake_urlopen(url, timeout):
        requested["url"] = url
        n = url.split("latitude=")[1].split("&")[0].count("%2C") + 1
        return _fake_response([45.0] * n, wind_from=90.0)  # easterly: a headwind

    monkeypatch.setattr(wind, "urlopen", fake_urlopen)
    start = datetime(2026, 7, 12, 8, 0, tzinfo=timezone.utc)
    field = fetch_open_meteo(course, start, timedelta(hours=1), n_stations=4)

    assert "wind_speed_unit=ms" in requested["url"]
    assert field.u.shape == (4, 24)
    u, v = field.sample(course.lat_mid, course.lon_mid, np.full(course.n, start.timestamp()))
    assert head_wind_component(u, v, course.heading) == pytest.approx(4.0, abs=1e-6)
    assert field.mean_temperature(start.timestamp(), start.timestamp() + 3600) == pytest.approx(22.25)


def test_gridded_wind_interpolates_in_time_and_space():
    t0 = 1_700_000_000.0
    field = GriddedWind(
        lat=np.array([45.0, 45.0]),
        lon=np.array([6.0, 7.0]),
        times=np.array([t0, t0 + 3600.0]),
        u=np.array([[0.0, 2.0], [4.0, 6.0]]),
        v=np.zeros((2, 2)),
    )
    u, _ = field.sample(np.array([45.0]), np.array([6.0]), np.array([t0 + 1800.0]))
    assert u[0] == pytest.approx(1.0, abs=1e-6)  # at station 0, halfway between hours
    u, _ = field.sample(np.array([45.0]), np.array([6.5]), np.array([t0]))
    assert u[0] == pytest.approx(2.0, abs=1e-6)  # midway between stations
