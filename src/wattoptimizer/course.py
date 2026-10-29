"""Course geometry: load GPX/FIT routes and resample them onto a uniform distance grid."""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter1d

EARTH_RADIUS_M = 6_371_000.0
_FINE_STEP_M = 5.0


@dataclass(frozen=True)
class Course:
    """A route discretised into ``n`` segments joining ``n + 1`` nodes.

    Node arrays (``lat``, ``lon``, ``east``, ``north``, ``elevation``) have length ``n + 1``;
    derived segment properties (``seg_length``, ``grade``, ``heading`` ...) have length ``n``.
    ``east``/``north`` are metres in a local tangent plane centred on the route.
    """

    lat: np.ndarray
    lon: np.ndarray
    east: np.ndarray
    north: np.ndarray
    elevation: np.ndarray
    name: str = "course"

    @property
    def n(self) -> int:
        return len(self.elevation) - 1

    @property
    def run(self) -> np.ndarray:
        """Horizontal length of each segment [m]."""
        return np.hypot(np.diff(self.east), np.diff(self.north))

    @property
    def rise(self) -> np.ndarray:
        return np.diff(self.elevation)

    @property
    def seg_length(self) -> np.ndarray:
        """Along-road (3D) length of each segment [m]."""
        return np.hypot(self.run, self.rise)

    @property
    def distance(self) -> np.ndarray:
        """Cumulative along-road distance at each node [m]."""
        return np.concatenate([[0.0], np.cumsum(self.seg_length)])

    @property
    def total_distance(self) -> float:
        return float(self.seg_length.sum())

    @property
    def elevation_gain(self) -> float:
        return float(np.clip(self.rise, 0.0, None).sum())

    @property
    def grade(self) -> np.ndarray:
        """Rise over run of each segment."""
        return self.rise / self.run

    @property
    def sin_slope(self) -> np.ndarray:
        return self.rise / self.seg_length

    @property
    def cos_slope(self) -> np.ndarray:
        return self.run / self.seg_length

    @property
    def heading(self) -> np.ndarray:
        """Direction of travel of each segment [rad, clockwise from north]."""
        return np.arctan2(np.diff(self.east), np.diff(self.north))

    @property
    def lat_mid(self) -> np.ndarray:
        return 0.5 * (self.lat[:-1] + self.lat[1:])

    @property
    def lon_mid(self) -> np.ndarray:
        return 0.5 * (self.lon[:-1] + self.lon[1:])

    @classmethod
    def from_points(
        cls,
        lat,
        lon,
        elevation,
        *,
        ds: float = 100.0,
        smoothing: float = 100.0,
        name: str = "course",
    ) -> Course:
        """Resample raw track points onto ``round(length / ds)`` equal segments.

        ``smoothing`` is the standard deviation [m] of a Gaussian filter applied to
        elevation, which suppresses GPS/barometer noise that would otherwise show up
        as spurious steep gradients.
        """
        lat = np.asarray(lat, dtype=float)
        lon = np.asarray(lon, dtype=float)
        ele = _fill_missing(np.asarray(elevation, dtype=float))

        lat0 = np.radians(lat.mean())
        east = EARTH_RADIUS_M * np.cos(lat0) * np.radians(lon - lon[0])
        north = EARTH_RADIUS_M * np.radians(lat - lat[0])

        keep = np.concatenate([[True], np.hypot(np.diff(east), np.diff(north)) > 0.01])
        lat, lon, ele, east, north = (a[keep] for a in (lat, lon, ele, east, north))
        h = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(east), np.diff(north)))])
        if h[-1] < ds:
            raise ValueError(f"course is only {h[-1]:.0f} m long, shorter than the grid spacing ds={ds} m")

        fine = np.append(np.arange(0.0, h[-1], _FINE_STEP_M), h[-1])
        z_fine = np.interp(fine, h, ele)
        if smoothing > 0:
            z_fine = gaussian_filter1d(z_fine, smoothing / _FINE_STEP_M, mode="nearest")

        grid = np.linspace(0.0, h[-1], max(1, round(h[-1] / ds)) + 1)
        return cls(
            lat=np.interp(grid, h, lat),
            lon=np.interp(grid, h, lon),
            east=np.interp(grid, h, east),
            north=np.interp(grid, h, north),
            elevation=np.interp(grid, fine, z_fine),
            name=name,
        )

    @classmethod
    def from_profile(
        cls,
        distance,
        elevation,
        heading_deg=0.0,
        *,
        lat0: float = 45.0,
        lon0: float = 6.0,
        ds: float = 100.0,
        smoothing: float = 0.0,
        name: str = "synthetic",
    ) -> Course:
        """Build a course from a horizontal-distance/elevation profile.

        ``heading_deg`` (scalar or one value per point) is the direction of travel
        leaving each point. Handy for tests and what-if studies.
        """
        distance = np.asarray(distance, dtype=float)
        heading = np.radians(np.broadcast_to(np.asarray(heading_deg, dtype=float), distance.shape))
        step = np.diff(distance)
        east = np.concatenate([[0.0], np.cumsum(step * np.sin(heading[:-1]))])
        north = np.concatenate([[0.0], np.cumsum(step * np.cos(heading[:-1]))])
        lat = lat0 + np.degrees(north / EARTH_RADIUS_M)
        lon = lon0 + np.degrees(east / (EARTH_RADIUS_M * np.cos(np.radians(lat0))))
        return cls.from_points(lat, lon, elevation, ds=ds, smoothing=smoothing, name=name)


def load_course(path: str | Path, *, ds: float = 100.0, smoothing: float = 100.0) -> Course:
    """Load a ``.gpx`` or ``.fit`` route/activity and resample it (see :meth:`Course.from_points`)."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".gpx":
        lat, lon, ele, name = _read_gpx(path)
    elif suffix == ".fit":
        lat, lon, ele, name = _read_fit(path)
    else:
        raise ValueError(f"unsupported course format {suffix!r}; expected .gpx or .fit")
    if len(lat) < 2:
        raise ValueError(f"{path} contains fewer than two positioned points")
    return Course.from_points(lat, lon, ele, ds=ds, smoothing=smoothing, name=name)


def _read_gpx(path: Path):
    import gpxpy

    with open(path, encoding="utf-8") as fh:
        gpx = gpxpy.parse(fh)
    points = [p for trk in gpx.tracks for seg in trk.segments for p in seg.points]
    if not points:
        points = [p for route in gpx.routes for p in route.points]
    name = next((t.name for t in gpx.tracks if t.name), None) or gpx.name or path.stem
    return (
        [p.latitude for p in points],
        [p.longitude for p in points],
        [np.nan if p.elevation is None else p.elevation for p in points],
        name,
    )


def _read_fit(path: Path):
    try:
        import fitdecode
    except ImportError as exc:
        raise ImportError("reading .fit files needs: pip install 'wattoptimizer[fit]'") from exc

    semicircle = 180.0 / 2**31
    lat, lon, ele = [], [], []
    with fitdecode.FitReader(path) as fit:
        for frame in fit:
            if not isinstance(frame, fitdecode.FitDataMessage) or frame.name != "record":
                continue
            la = frame.get_value("position_lat", fallback=None)
            lo = frame.get_value("position_long", fallback=None)
            if la is None or lo is None:
                continue
            alt = frame.get_value("enhanced_altitude", fallback=None)
            if alt is None:
                alt = frame.get_value("altitude", fallback=None)
            lat.append(la * semicircle)
            lon.append(lo * semicircle)
            ele.append(np.nan if alt is None else alt)
    return lat, lon, ele, path.stem


def _fill_missing(ele: np.ndarray) -> np.ndarray:
    ok = np.isfinite(ele)
    if ok.all():
        return ele
    if not ok.any():
        warnings.warn("course has no elevation data; treating it as flat", stacklevel=3)
        return np.zeros_like(ele)
    idx = np.arange(len(ele))
    return np.interp(idx, idx[ok], ele[ok])
