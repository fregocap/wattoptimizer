"""A synthetic 40 km out-and-back time trial, for trying the tool without a GPX of your own."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .course import EARTH_RADIUS_M

LAT0, LON0 = 45.0, 6.0


def demo_profile(step: float = 20.0):
    """Horizontal distance, elevation and heading of the demo course.

    Rolling terrain, a 1.5 km climb at ~6% between km 8 and 9.5, and a turnaround
    at km 20: riders head east on the way out and west on the way back.
    """
    x = np.arange(0.0, 40_000.0 + step, step)
    out = np.minimum(x, 40_000.0 - x)  # position along the outbound leg
    climb = np.clip(out - 8_000.0, 0.0, 1_500.0) * 0.06
    elevation = 150.0 + 25.0 * np.sin(out / 1_800.0) + 10.0 * np.sin(out / 430.0) + climb
    heading = np.where(x < 20_000.0, 90.0, 270.0)
    return x, elevation, heading


def write_demo_gpx(path: str | Path) -> Path:
    import gpxpy.gpx

    x, elevation, heading = demo_profile()
    step = np.diff(x)
    rad = np.radians(heading[:-1])
    east = np.concatenate([[0.0], np.cumsum(step * np.sin(rad))])
    north = np.concatenate([[0.0], np.cumsum(step * np.cos(rad))])
    # offset the return leg 8 m south so the two carriageways do not overlap
    north = north - np.where(x > 20_000.0, 8.0, 0.0)
    lat = LAT0 + np.degrees(north / EARTH_RADIUS_M)
    lon = LON0 + np.degrees(east / (EARTH_RADIUS_M * np.cos(np.radians(LAT0))))

    gpx = gpxpy.gpx.GPX()
    track = gpxpy.gpx.GPXTrack(name="WattOptimizer demo 40 km TT")
    segment = gpxpy.gpx.GPXTrackSegment()
    segment.points.extend(gpxpy.gpx.GPXTrackPoint(la, lo, elevation=z) for la, lo, z in zip(lat, lon, elevation))
    track.segments.append(segment)
    gpx.tracks.append(track)

    path = Path(path)
    path.write_text(gpx.to_xml(), encoding="utf-8")
    return path
