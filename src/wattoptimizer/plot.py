"""Static chart of a plan: stacked panels sharing the distance axis (needs matplotlib)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .optimizer import Plan

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
GRID = "#e4e3df"
SERIES = "#2a78d6"
TERRAIN = "#b9b8b2"
CLIMB_BAND = "#eeede9"


def plot_plan(plan: Plan, path: str | Path) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise ImportError("plotting needs: pip install 'wattoptimizer[plot]'") from exc

    km = plan.course.distance / 1000.0
    seg_km = np.repeat(km, 2)[1:-1]  # step outline for segment-wise quantities
    power = np.repeat(plan.power, 2)

    fig, axes = plt.subplots(5, 1, figsize=(11, 10), sharex=True, height_ratios=[1.0, 1.6, 1.0, 1.0, 1.0])
    fig.patch.set_facecolor(SURFACE)
    elevation, watts, speed, w_bal, glycogen = axes

    elevation.fill_between(km, plan.course.elevation, plan.course.elevation.min() - 5, color=TERRAIN, lw=0)
    elevation.set_ylabel("elevation [m]")

    # sitting-up sections as recessive bands behind the power trace
    for start, end in _runs(plan.climbing):
        watts.axvspan(km[start], km[end], color=CLIMB_BAND, lw=0, zorder=0)
    watts.plot(seg_km, power, color=SERIES, lw=2)
    # CP as ridden: lower on the aero bars, and declining with durability
    cp = np.repeat(plan.cp_effective, 2)
    watts.plot(seg_km, cp, color=INK_SECONDARY, lw=1, ls=(0, (4, 3)))
    watts.annotate("CP", (km[-1], cp[-1]), xytext=(4, 0), textcoords="offset points",
                   va="center", color=INK_SECONDARY, fontsize=9)
    watts.set_ylabel("target power [W]")
    if plan.climbing.any():
        watts.set_title("shaded: seated upright", loc="right", fontsize=9, color=INK_SECONDARY)

    speed.plot(km, 3.6 * plan.speed, color=SERIES, lw=2)
    speed.set_ylabel("speed [km/h]")
    w_bal.plot(km, plan.w_bal / 1000.0, color=SERIES, lw=2)
    w_bal.set_ylabel("W'bal [kJ]")
    w_bal.set_ylim(bottom=0)
    glycogen.plot(km, plan.glycogen, color=SERIES, lw=2)
    glycogen.set_ylabel("glycogen [g]")
    glycogen.set_xlabel("distance [km]")

    for ax in axes:
        ax.set_facecolor(SURFACE)
        ax.grid(axis="y", color=GRID, lw=0.8)
        ax.tick_params(colors=INK_SECONDARY, labelsize=9)
        ax.yaxis.label.set_color(INK)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(GRID)
    glycogen.set_xlim(km[0], km[-1])
    fig.suptitle(f"{plan.course.name}: pacing plan", x=0.01, ha="left", color=INK, fontsize=13)
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def _runs(mask: np.ndarray):
    """(start, end) node indices of consecutive True segments."""
    edges = np.diff(np.concatenate([[0], mask.astype(int), [0]]))
    return zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1))
