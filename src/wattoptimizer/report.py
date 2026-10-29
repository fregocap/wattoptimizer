"""Turn a :class:`~wattoptimizer.optimizer.Plan` into per-split wattage targets."""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from .optimizer import Plan
from .simulate import SimResult


@dataclass
class Split:
    start_km: float
    end_km: float
    grade_pct: float
    gain_m: float
    target_w: float  # time-weighted mean power, rounded to 5 W
    speed_kmh: float
    split_s: float
    elapsed_s: float
    w_bal_kj: float  # at the end of the split
    glycogen_g: float  # at the end of the split
    position: str  # aero / climb / mixed
    head_wind_ms: float


def splits(plan: Plan, every: float = 1000.0) -> list[Split]:
    """Aggregate segments into splits of ``every`` metres (the last one may be shorter)."""
    course = plan.course
    start = course.distance[:-1]
    dt = plan.seg_time
    length = course.seg_length
    bins = np.minimum((start // every).astype(int), int(np.ceil(course.total_distance / every)) - 1)

    out = []
    for b in np.unique(bins):
        seg = np.flatnonzero(bins == b)
        first, last = seg[0], seg[-1] + 1
        time = dt[seg].sum()
        climb_share = (dt[seg] * plan.climbing[seg]).sum() / time
        out.append(
            Split(
                start_km=course.distance[first] / 1000.0,
                end_km=course.distance[last] / 1000.0,
                grade_pct=100.0 * course.rise[seg].sum() / course.run[seg].sum(),
                gain_m=float(np.clip(course.rise[seg], 0.0, None).sum()),
                target_w=5.0 * round((plan.power[seg] * dt[seg]).sum() / time / 5.0),
                speed_kmh=3.6 * length[seg].sum() / time,
                split_s=float(time),
                elapsed_s=float(plan.time[last]),
                w_bal_kj=plan.w_bal[last] / 1000.0,
                glycogen_g=float(plan.glycogen[last]),
                position="climb" if climb_share > 0.75 else "aero" if climb_share < 0.25 else "mixed",
                head_wind_ms=float((plan.head_wind[seg] * dt[seg]).sum() / time),
            )
        )
    return out


def format_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def format_table(rows: list[Split]) -> str:
    header = f"{'km':>11} {'grade':>6} {'target':>7} {'speed':>6} {'wind':>5} {'split':>6} {'elapsed':>8} {'Wbal':>5} {'CHO':>4}  pos"
    units = f"{'':>11} {'%':>6} {'W':>7} {'km/h':>6} {'m/s':>5} {'':>6} {'':>8} {'kJ':>5} {'g':>4}"
    lines = [header, units, "-" * len(header)]
    for r in rows:
        lines.append(
            f"{r.start_km:5.1f}-{r.end_km:5.1f} {r.grade_pct:6.1f} {r.target_w:7.0f} {r.speed_kmh:6.1f} "
            f"{r.head_wind_ms:5.1f} {format_duration(r.split_s):>6} {format_duration(r.elapsed_s):>8} "
            f"{r.w_bal_kj:5.1f} {r.glycogen_g:4.0f}  {r.position}"
        )
    return "\n".join(lines)


def summary(plan: Plan, baseline: tuple[float, SimResult] | None = None) -> str:
    a, course = plan.athlete, plan.course
    lines = [
        f"Course     {course.name}: {course.total_distance / 1000:.1f} km, +{course.elevation_gain:.0f} m",
        f"Finish     {format_duration(plan.total_time)}  "
        f"(avg {plan.avg_power:.0f} W = {plan.avg_power / a.cp:.0%} CP, "
        f"{3.6 * course.total_distance / plan.total_time:.1f} km/h, {plan.work / 1000:.0f} kJ)",
        f"Reserves   W'bal {plan.w_bal[-1] / 1000:.1f} kJ ({plan.w_bal[-1] / a.w_prime:.0%}), "
        f"glycogen {plan.glycogen[-1]:.0f} g at the finish",
    ]
    t = plan.seg_time
    changes = int(np.count_nonzero(np.diff(plan.climbing.astype(np.int8))))
    position = f"upright {(t * plan.climbing).sum() / plan.total_time:.0%} of the time, {changes} position changes"
    if plan.relaxed_time is not None:
        position += f" (optimised; rounding to aero/upright cost {plan.total_time - plan.relaxed_time:.1f} s)"
    lines.append(f"Position   {position}")
    if baseline is not None:
        power, sim = baseline
        saved = sim.total_time - plan.total_time
        lines.append(
            f"Even pace  {power:.0f} W constant meets the same reserves in {format_duration(sim.total_time)} "
            f"-> plan saves {saved:.0f} s"
        )
    if not plan.converged:
        lines.append("Note       wind-timing/position iterations hit the limit; the plan is usable but not settled")
    return "\n".join(lines)


def write_csv(rows: list[Split], path: str | Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(asdict(rows[0])))
        writer.writeheader()
        writer.writerows(asdict(r) for r in rows)
