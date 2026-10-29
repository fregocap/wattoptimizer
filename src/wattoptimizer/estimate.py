"""Estimate CP and W' without a lab: from a ride file's best efforts or from intervals.icu.

The two-parameter critical power model says the work you can do in ``t`` seconds of
all-out effort is ``W = CP * t + W'``. Given best average powers for a few durations
between about 2 and 20 minutes, a straight-line fit of work against time gives CP
(the slope) and W' (the intercept). It is only as good as the efforts: they must be
genuinely maximal, ideally from different days or a structured test.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import export

# durations used for the fit [s]: long enough to be past the sprint, short enough to be maximal
FIT_DURATIONS = (120, 180, 300, 480, 720, 1200)
W_PRIME_RANGE = (3_000.0, 60_000.0)  # J; outside this the efforts weren't all-out
TYPICAL_W_PRIME = 20_000.0  # J, used when only an FTP is known


@dataclass(frozen=True)
class Estimate:
    cp: float | None  # W
    w_prime: float | None  # J
    weight: float | None  # kg
    ftp: float | None  # W
    efforts: list[tuple[int, float]]  # (seconds, best average watts) used for the fit
    note: str  # how the numbers were obtained, shown to the user


def fit_cp(durations, watts) -> tuple[float, float] | None:
    """Least-squares CP [W] and W' [J] from best average powers; None if they don't fit the model."""
    t = np.asarray(durations, dtype=float)
    p = np.asarray(watts, dtype=float)
    keep = (t >= 120) & (t <= 1800) & (p > 0)
    t, p = t[keep], p[keep]
    if len(t) < 3 or t.max() / t.min() < 3:
        return None
    cp, w_prime = np.polyfit(t, p * t, 1)
    if cp <= 0 or not W_PRIME_RANGE[0] <= w_prime <= W_PRIME_RANGE[1]:
        return None
    return float(cp), float(w_prime)


def mean_max(power, durations=FIT_DURATIONS, dt: float = 1.0) -> list[tuple[int, float]]:
    """Best average power for each duration in a 1 Hz (or ``dt``-spaced) power series."""
    p = np.nan_to_num(np.asarray(power, dtype=float), nan=0.0)
    csum = np.concatenate([[0.0], np.cumsum(p)])
    out = []
    for d in durations:
        n = int(round(d / dt))
        if 0 < n <= len(p):
            out.append((int(d), float((csum[n:] - csum[:-n]).max() / n)))
    return out


def ride_power(path: str | Path) -> np.ndarray:
    """Power from a .fit ride file, resampled to 1 Hz (gaps count as zero, as when coasting)."""
    try:
        import fitdecode
    except ImportError as exc:
        raise ImportError("reading .fit files needs: pip install 'wattoptimizer[fit]'") from exc
    times, watts = [], []
    with fitdecode.FitReader(path) as fit:
        for frame in fit:
            if not isinstance(frame, fitdecode.FitDataMessage) or frame.name != "record":
                continue
            ts, pw = frame.get_value("timestamp", fallback=None), frame.get_value("power", fallback=None)
            if ts is not None and pw is not None:
                times.append(ts.timestamp())
                watts.append(float(pw))
    if len(times) < 120:
        raise ValueError("this file has no power data (or under two minutes of it)")
    t = np.asarray(times) - times[0]
    grid = np.arange(0.0, t[-1] + 1.0)
    idx = np.searchsorted(t, grid, side="right") - 1
    held = np.asarray(watts)[idx]
    # don't carry a value across a pause longer than 5 s
    held[(grid - t[idx]) > 5.0] = 0.0
    return held


def from_ride(path: str | Path) -> Estimate:
    efforts = mean_max(ride_power(path))
    fit = fit_cp(*zip(*efforts)) if len(efforts) >= 3 else None
    if fit is None:
        return Estimate(None, None, None, None, efforts,
                        "the best efforts in this ride don't fit the CP model; use a ride with hard 3–20 min efforts")
    return Estimate(fit[0], fit[1], None, None, efforts,
                    f"fitted to your best {efforts[0][0] // 60}–{efforts[-1][0] // 60} min efforts in this ride")


def from_intervals(api_key: str, athlete_id: str = "0", timeout: float = 30.0) -> Estimate:
    """Weight and FTP from intervals.icu, plus CP and W' fitted to the 90-day power curve when it has one."""
    athlete = export.intervals_get(f"/athlete/{athlete_id}", api_key, timeout)
    weight = athlete.get("icu_weight") or athlete.get("weight")
    ride = next((s for s in athlete.get("sportSettings") or [] if "Ride" in (s.get("types") or [])), {})
    ftp = ride.get("ftp")

    efforts: list[tuple[int, float]] = []
    curves = export.intervals_get(f"/athlete/{athlete_id}/power-curves?type=Ride&curves=90d", api_key, timeout)
    if curves.get("list"):
        curve = curves["list"][0]
        lookup = dict(zip(curve.get("secs") or [], curve.get("watts") or []))
        efforts = [(d, float(lookup[d])) for d in FIT_DURATIONS if lookup.get(d)]
    fit = fit_cp(*zip(*efforts)) if len(efforts) >= 3 else None
    if fit is not None:
        return Estimate(fit[0], fit[1], weight, ftp, efforts, "fitted to your 90-day power curve on intervals.icu")
    if ftp:
        # CP sits close to a well-tested FTP; W' falls back to a typical value
        return Estimate(float(ftp), TYPICAL_W_PRIME, weight, ftp, efforts,
                        "CP set to your intervals.icu FTP (no power curve available); W′ is a typical value")
    return Estimate(None, None, weight, None, efforts, "intervals.icu has no FTP or power curve for you yet")
