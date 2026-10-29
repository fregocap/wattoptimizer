"""Forward simulation of a given power plan, using the same discretisation as the optimiser."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import brentq

from . import physics, physiology
from .athlete import Athlete, RaceSpec
from .course import Course


@dataclass
class SimResult:
    speed: np.ndarray  # nodes [m/s]
    time: np.ndarray  # nodes [s]
    w_bal: np.ndarray  # nodes [J]
    glycogen: np.ndarray  # nodes [g]
    brake_force: np.ndarray  # segments [N]
    stalled: bool  # the rider could not keep moving at some point
    work_counted: np.ndarray  # nodes [J], the durability work counter
    cp_effective: np.ndarray  # segments [W], CP after position and durability

    @property
    def total_time(self) -> float:
        return float(self.time[-1])


def simulate(
    course: Course,
    athlete: Athlete,
    race: RaceSpec,
    power: np.ndarray,
    *,
    head_wind: np.ndarray | None = None,
    cda: np.ndarray | None = None,
    cp: np.ndarray | None = None,
    v_max: np.ndarray | None = None,
    v_floor: float = 0.5,
) -> SimResult:
    """Ride ``power`` (one value per segment) over the course.

    ``cp`` is the fresh CP of each segment (it depends on the riding position);
    durability is applied on top. Where the speed would exceed ``v_max`` the rider
    brakes just enough to hold it.
    """
    n = course.n
    power = np.broadcast_to(np.asarray(power, dtype=float), (n,))
    head_wind = np.zeros(n) if head_wind is None else head_wind
    cda = np.full(n, athlete.cda_aero) if cda is None else cda
    cp = np.full(n, athlete.cp) if cp is None else cp
    v_max = np.full(n + 1, np.inf) if v_max is None else v_max
    sp = physics.segment_params(course, athlete, race.temperature)
    w0 = athlete.w_prime

    v = np.empty(n + 1)
    t = np.zeros(n + 1)
    w = np.empty(n + 1)
    g = np.empty(n + 1)
    brake = np.zeros(n)
    work = np.zeros(n + 1)
    cp_eff = np.empty(n)
    v[0], w[0], g[0] = min(race.start_speed, v_max[0]), w0, athlete.glycogen
    stalled = False

    for k in range(n):
        seg = _segment(sp, k)

        def residual(v1, brake_force=0.0):
            return physics.energy_residual(v[k], v1, power[k], head_wind[k], cda[k], brake_force, seg)

        if residual(v_floor) <= 0.0:
            v[k + 1], stalled = v_floor, True
        else:
            v[k + 1] = brentq(residual, v_floor, 100.0, xtol=1e-9)
        if v[k + 1] > v_max[k + 1]:
            v[k + 1] = v_max[k + 1]
            brake[k] = max(0.0, residual(v[k + 1]) / seg.length)

        dt = 2.0 * seg.length / (v[k] + v[k + 1])
        t[k + 1] = t[k] + dt
        work[k + 1] = work[k] + physiology.counted_work_rate(power[k], athlete) * dt
        work_mid = 0.5 * (work[k] + work[k + 1])
        cp_eff[k] = cp[k] * physiology.cp_factor(work_mid, athlete)
        capacity = w0 * physiology.w_prime_factor(np.array([work[k], work_mid, work[k + 1]]), athlete)
        # implicit midpoint for W'bal: the rate is affine in the end value, so solve exactly.
        # Capacity lost to durability is lost from the balance too (capacity[2] - capacity[0]).
        excess, deficit = max(power[k] - cp_eff[k], 0.0), max(cp_eff[k] - power[k], 0.0)
        w[k + 1] = (
            w[k] + dt * physiology.w_prime_rate(excess, deficit, 0.5 * w[k], capacity[1]) + capacity[2] - capacity[0]
        ) / (1.0 + dt * deficit / (2.0 * capacity[1]))
        cho = physiology.cho_oxidation(power[k], athlete, cp_factor=physiology.cp_factor(work_mid, athlete))
        g[k + 1] = g[k] + dt * (athlete.cho_intake / 3600.0 - cho)

    return SimResult(speed=v, time=t, w_bal=w, glycogen=g, brake_force=brake, stalled=stalled,
                     work_counted=work, cp_effective=cp_eff)


def meets_reserves(sim: SimResult, athlete: Athlete, race: RaceSpec, tol: float = 1e-6) -> bool:
    """True if the ride never stalls or runs W'bal/glycogen dry and finishes with the race's reserves."""
    return bool(
        not sim.stalled
        and sim.w_bal.min() >= -tol
        and sim.w_bal[-1] >= race.w_prime_final * athlete.w_prime - tol
        and sim.glycogen.min() >= -tol
        and sim.glycogen[-1] >= race.glycogen_final - tol
    )


def best_even_power(
    course: Course,
    athlete: Athlete,
    race: RaceSpec,
    **sim_kwargs,
) -> tuple[float, SimResult] | None:
    """Highest constant power that still meets the race's W' and glycogen reserves.

    This is the honest 'even pacing' baseline to compare an optimised plan against.
    Returns ``None`` when even zero power cannot meet the reserves.
    """

    def feasible(p: float) -> tuple[bool, SimResult]:
        sim = simulate(course, athlete, race, p, **sim_kwargs)
        return meets_reserves(sim, athlete, race), sim

    lo, hi = 0.0, athlete.p_max
    ok, best = feasible(lo)
    if not ok:
        # zero power stalls on climbs; look for any feasible power before bisecting
        for p in np.linspace(athlete.p_max / 20, athlete.p_max, 20):
            ok, sim = feasible(p)
            if ok:
                lo, best = p, sim
                break
        else:
            return None
    while hi - lo > 0.5:
        mid = 0.5 * (lo + hi)
        ok, sim = feasible(mid)
        if ok:
            lo, best = mid, sim
        else:
            hi = mid
    return lo, best


def _segment(sp: physics.SegmentParams, k: int) -> physics.SegmentParams:
    return physics.SegmentParams(
        length=sp.length[k],
        sin_slope=sp.sin_slope[k],
        cos_slope=sp.cos_slope[k],
        rho=sp.rho[k],
        mass=sp.mass,
        effective_mass=sp.effective_mass,
        crr=sp.crr,
        efficiency=sp.efficiency,
    )
