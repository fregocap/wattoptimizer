"""Wind fields and the Open-Meteo forecast/archive client.

Wind vectors are stored as (u, v) = (eastward, northward) air velocity at 10 m [m/s],
i.e. the direction the air moves *towards*. Meteorological directions ("wind from
270 deg") are converted on the way in.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol
from urllib.parse import urlencode
from urllib.request import urlopen

import numpy as np

from .course import Course

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


class WindField(Protocol):
    def sample(self, lat: np.ndarray, lon: np.ndarray, t: np.ndarray | None) -> tuple[np.ndarray, np.ndarray]:
        """(u, v) at 10 m for points ``lat``/``lon`` [deg] at unix times ``t`` [s]."""
        ...


def from_direction(speed, direction_from_deg):
    """(u, v) components for a wind of ``speed`` blowing *from* ``direction_from_deg``."""
    rad = np.radians(direction_from_deg)
    return -speed * np.sin(rad), -speed * np.cos(rad)


def head_wind_component(u, v, heading):
    """Head wind [m/s] felt when travelling along ``heading`` [rad]; negative is a tailwind."""
    return -(u * np.sin(heading) + v * np.cos(heading))


@dataclass(frozen=True)
class ConstantWind:
    speed: float  # m/s at 10 m
    direction_from: float  # deg, meteorological convention

    def sample(self, lat, lon, t):
        u, v = from_direction(self.speed, self.direction_from)
        shape = np.shape(lat)
        return np.full(shape, u), np.full(shape, v)


@dataclass(frozen=True)
class GriddedWind:
    """Hourly wind at a handful of stations, interpolated linearly in time and by
    inverse-distance weighting in space."""

    lat: np.ndarray  # (S,)
    lon: np.ndarray  # (S,)
    times: np.ndarray  # (T,) unix seconds
    u: np.ndarray  # (S, T)
    v: np.ndarray  # (S, T)
    temperature: np.ndarray | None = None  # (S, T) [C]

    def sample(self, lat, lon, t):
        if t is None:
            raise ValueError("a gridded wind field needs absolute times; set the race start time")
        lat, lon, t = np.atleast_1d(lat), np.atleast_1d(lon), np.atleast_1d(t)
        u_t = np.array([np.interp(t, self.times, row) for row in self.u])  # (S, Q)
        v_t = np.array([np.interp(t, self.times, row) for row in self.v])
        weights = self._weights(lat, lon)
        return (weights * u_t).sum(axis=0), (weights * v_t).sum(axis=0)

    def mean_temperature(self, t_start: float, t_end: float) -> float | None:
        if self.temperature is None:
            return None
        window = (self.times >= t_start - 3600) & (self.times <= t_end + 3600)
        return float(self.temperature[:, window].mean()) if window.any() else float(self.temperature.mean())

    def _weights(self, lat, lon):
        dy = np.radians(lat[None, :] - self.lat[:, None])
        dx = np.radians(lon[None, :] - self.lon[:, None]) * np.cos(np.radians(lat))[None, :]
        inv = 1.0 / (dx * dx + dy * dy + 1e-12)
        return inv / inv.sum(axis=0, keepdims=True)


def fetch_open_meteo(
    course: Course,
    start: datetime,
    duration: timedelta,
    *,
    n_stations: int = 6,
    timeout: float = 30.0,
) -> GriddedWind:
    """Fetch hourly 10 m wind and temperature along the course from Open-Meteo (no API key).

    Dates more than five days in the past are served from the historical archive, so
    past races can be re-analysed with the conditions on the day.
    """
    idx = np.unique(np.linspace(0, course.n, n_stations).round().astype(int))
    lat, lon = course.lat[idx], course.lon[idx]
    start = start.astimezone(timezone.utc)
    end = start + duration
    archive = end.date() < (datetime.now(timezone.utc) - timedelta(days=5)).date()
    query = urlencode(
        {
            "latitude": ",".join(f"{x:.4f}" for x in lat),
            "longitude": ",".join(f"{x:.4f}" for x in lon),
            "hourly": "wind_speed_10m,wind_direction_10m,temperature_2m",
            "wind_speed_unit": "ms",
            "timezone": "UTC",
            "start_date": start.date().isoformat(),
            "end_date": end.date().isoformat(),
        }
    )
    with urlopen(f"{ARCHIVE_URL if archive else FORECAST_URL}?{query}", timeout=timeout) as response:
        payload = json.load(response)
    stations = payload if isinstance(payload, list) else [payload]

    times = np.array(
        [datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() for s in stations[0]["hourly"]["time"]]
    )
    speed = np.array([s["hourly"]["wind_speed_10m"] for s in stations], dtype=float)
    direction = np.array([s["hourly"]["wind_direction_10m"] for s in stations], dtype=float)
    temperature = np.array([s["hourly"]["temperature_2m"] for s in stations], dtype=float)
    if np.isnan(speed).all():
        raise ValueError("Open-Meteo returned no wind data for the requested time window")
    speed, direction, temperature = (np.nan_to_num(a, nan=np.nanmean(a)) for a in (speed, direction, temperature))
    u, v = from_direction(speed, direction)
    return GriddedWind(lat=lat, lon=lon, times=times, u=u, v=v, temperature=temperature)
