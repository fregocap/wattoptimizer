"""Minimum-time pacing as a direct-collocation optimal control problem.

Distance s is the independent variable. On a grid of n segments the decision
variables are

    nodes:     speed v_k, W'bal w_k, glycogen g_k, elapsed time t_k
    segments:  power P_k (the control u), brake force b_k, position a_k

where a_k in [0, 1] blends the aero (0) and seated-upright (1) positions:

    CdA_k = CdA_aero + a_k (CdA_up - CdA_aero),   CP_k = CP (f_aero + a_k (1 - f_aero))

and the NLP is

    minimise   t_n  +  smoothing * mean((P_{k+1} - P_k)^2 / CP^2)
    subject to work-energy balance on every segment         (physics.energy_residual)
               W'bal and glycogen dynamics                   (physiology)
               v_0 = v_start, w_0 = W', g_0 = G_0, t_0 = 0
               0 <= P <= P_max, 0 <= w, 0 <= g, v_min <= v <= v_max(s)
               w_n >= target W'bal,  g_n >= target glycogen  (arrive at T2 with reserves)

solved with IPOPT through CasADi. The position choice is binary in reality; with
``position_mode = "optimize"`` it is first solved as the continuous relaxation above,
then rounded (merging position changes shorter than ``min_position_run``) and the
NLP re-solved with positions fixed. With ``"rules"`` it follows grade/speed thresholds.

The wind each segment sees depends on the time it is ridden, i.e. on the solution;
this is resolved by a short fixed-point loop around the NLP.
"""

from __future__ import annotations

from dataclasses import dataclass

import casadi as ca
import numpy as np
from scipy.ndimage import median_filter

from . import physics, physiology
from .athlete import Athlete, RaceSpec
from .course import Course
from .simulate import simulate
from .wind import WindField, head_wind_component

V_MIN = 1.0  # m/s, keeps dt = L / v finite
_GUESS_INTENSITY = 0.75
# small cost on |P - CP| that makes the excess/deficit split unique while W'bal is
# pinned at zero; biases the finish time by well under a second on a 40 km TT
_SPLIT_PENALTY = 1e-3
# discourages position chatter in the relaxed solve
_POSITION_PENALTY = 1e-3


class InfeasibleError(RuntimeError):
    """The race constraints cannot be met (e.g. the glycogen target is unreachable)."""


@dataclass
class SolverOptions:
    smoothing: float = 0.05  # penalty on segment-to-segment power changes
    outer_iterations: int = 4  # wind-timing / position fixed-point iterations
    max_iter: int = 3000  # IPOPT iterations per solve
    verbose: bool = False


@dataclass
class Plan:
    course: Course
    athlete: Athlete
    race: RaceSpec
    power: np.ndarray  # segments [W]
    speed: np.ndarray  # nodes [m/s]
    time: np.ndarray  # nodes [s]
    w_bal: np.ndarray  # nodes [J]
    glycogen: np.ndarray  # nodes [g]
    climbing: np.ndarray  # segments, True when sitting up
    cda: np.ndarray  # segments [m^2]
    head_wind: np.ndarray  # segments [m/s] at rider height
    brake_force: np.ndarray  # segments [N]
    v_max: np.ndarray  # nodes [m/s]
    iterations: int
    converged: bool
    relaxed_time: float | None = None  # finish time with fractional positions, if optimised

    @property
    def total_time(self) -> float:
        return float(self.time[-1])

    @property
    def seg_time(self) -> np.ndarray:
        return np.diff(self.time)

    @property
    def work(self) -> float:
        return float((self.power * self.seg_time).sum())

    @property
    def avg_power(self) -> float:
        return self.work / self.total_time

    @property
    def cp_segment(self) -> np.ndarray:
        return _cp_for(self.athlete, self.climbing)


def optimize(
    course: Course,
    athlete: Athlete,
    race: RaceSpec | None = None,
    wind: WindField | None = None,
    options: SolverOptions | None = None,
) -> Plan:
    """Compute the minimum-time power plan for ``course``."""
    race = race or RaceSpec()
    options = options or SolverOptions()
    if race.glycogen_final > athlete.glycogen + athlete.cho_intake * 24:
        raise InfeasibleError("glycogen_final exceeds anything the athlete could store")

    if race.position_mode not in ("optimize", "rules"):
        raise ValueError(f"position_mode must be 'optimize' or 'rules', not {race.position_mode!r}")

    vmax = physics.speed_limits(course, race)
    climbing = course.grade >= race.climb_grade

    def ride(power, head, climbing):
        """A dynamically consistent warm start: simulate ``power`` under the current parameters."""
        sim = simulate(course, athlete, race, power, head_wind=head, cda=_cda_for(athlete, climbing),
                       cp=_cp_for(athlete, climbing), v_max=vmax)
        return dict(v=sim.speed, p=power, w=np.maximum(sim.w_bal, 0.0), g=np.maximum(sim.glycogen, 0.0),
                    t=sim.time, b=sim.brake_force, a=climbing.astype(float))

    # start from even power, ridden twice so the wind is sampled at roughly the right times
    guess = ride(np.full(course.n, _GUESS_INTENSITY * athlete.cp), np.zeros(course.n), climbing)
    nlp = _Nlp(physics.segment_params(course, athlete, race.temperature), vmax, athlete, race, options,
               t_ref=guess["t"][-1])
    # "relaxed": positions free in [0, 1]; "fixed": rounded positions held; "rules": thresholds
    phase = "relaxed" if race.position_mode == "optimize" else "rules"
    sol, converged, relaxed_time = None, False, None
    # optimising positions needs a relaxed solve followed by at least one fixed one
    n_iter = max(options.outer_iterations, 2 if phase == "relaxed" else 1)
    for iteration in range(1, n_iter + 1):
        head = _head_wind(course, wind, race, guess["t"])
        guess = ride(guess["p"], head, climbing)
        new_sol = nlp.solve(head, guess, None if phase == "relaxed" else climbing)
        timing_settled = wind is None or (sol is not None and np.abs(new_sol["t"] - sol["t"]).max() < 2.0)
        sol = guess = new_sol

        if phase == "relaxed":
            relaxed_time = float(sol["t"][-1])
            climbing = _round_positions(course, sol["a"], race.min_position_run)
            # always leave room for at least one solve with the rounded positions
            if timing_settled or iteration >= n_iter - 1:
                phase = "fixed"
            continue

        new_climbing = climbing if phase == "fixed" else _choose_positions(course, sol["v"], race)
        converged = np.array_equal(new_climbing, climbing) and timing_settled
        if converged or iteration == n_iter:
            break  # keep climbing/head consistent with ``sol``
        climbing = new_climbing

    return Plan(
        course=course,
        athlete=athlete,
        race=race,
        power=sol["p"],
        speed=sol["v"],
        time=sol["t"],
        w_bal=sol["w"],
        glycogen=sol["g"],
        climbing=climbing,
        cda=_cda_for(athlete, climbing),
        head_wind=head,
        brake_force=guess["b"],
        v_max=vmax,
        iterations=iteration,
        converged=converged,
        relaxed_time=relaxed_time,
    )


class _Nlp:
    """The collocation NLP, built once and re-solved with updated parameters.

    Variables are scaled to O(1): power by CP, W'bal by W', glycogen by G_0,
    time by a reference duration and brake force by body weight.
    """

    def __init__(self, sp, vmax, athlete: Athlete, race: RaceSpec, options: SolverOptions, t_ref: float):
        n = len(sp.length)
        self.athlete, self.t_ref, self.weight = athlete, t_ref, sp.mass * physics.G
        w0, g0 = athlete.w_prime, athlete.glycogen
        o = self.opti = ca.Opti()

        self.v, self.w, self.g, self.t = (o.variable(n + 1) for _ in range(4))
        # power above/below CP, split so the W'bal switch at P = CP is exact; at the optimum
        # at most one is non-zero, since raising both together only drains W'bal faster
        self.p, self.b, self.excess, self.deficit = (o.variable(n) for _ in range(4))
        # position: 0 = aero, 1 = upright; its bounds are parameters so the same NLP serves
        # the relaxed solve (bounds 0..1) and the fixed one (lower = upper = rounded position)
        self.a = o.variable(n)
        self.a_lo, self.a_hi, self.head = o.parameter(n), o.parameter(n), o.parameter(n)
        cda = athlete.cda_aero + self.a * (athlete.cda_climb - athlete.cda_aero)
        cp_rel = athlete.aero_cp_factor + self.a * (1.0 - athlete.aero_cp_factor)  # CP_k / CP

        csp = sp.as_casadi()
        power = athlete.cp * self.p
        v0, v1 = self.v[:-1], self.v[1:]
        dt = csp.length / (0.5 * (v0 + v1))
        w_mid = 0.5 * (self.w[:-1] + self.w[1:]) * w0

        o.subject_to(
            physics.energy_residual(v0, v1, power, self.head, cda, self.b * self.weight, csp, xp=ca)
            / sp.effective_mass == 0
        )
        o.subject_to(self.p - cp_rel == self.excess - self.deficit)
        w_rate = physiology.w_prime_rate(athlete.cp * self.excess, athlete.cp * self.deficit, w_mid, w0)
        o.subject_to(self.w[1:] - self.w[:-1] == w_rate * dt / w0)
        cho_net = athlete.cho_intake / 3600.0 - physiology.cho_oxidation(power, athlete, xp=ca)
        o.subject_to(self.g[1:] - self.g[:-1] == cho_net * dt / g0)
        o.subject_to(self.t[1:] - self.t[:-1] == dt / t_ref)

        o.subject_to(self.v[0] == min(race.start_speed, vmax[0]))
        o.subject_to(self.w[0] == 1.0)
        o.subject_to(self.g[0] == 1.0)
        o.subject_to(self.t[0] == 0.0)
        o.subject_to(self.w[-1] >= race.w_prime_final)
        o.subject_to(self.g[-1] >= race.glycogen_final / g0)

        o.subject_to(self.v >= V_MIN)
        o.subject_to(self.v <= ca.DM(vmax))
        o.subject_to(o.bounded(0.0, self.p, athlete.p_max / athlete.cp))
        o.subject_to(self.w >= 0.0)
        o.subject_to(self.g >= 0.0)
        o.subject_to(self.b >= 0.0)
        o.subject_to(self.excess >= 0.0)
        o.subject_to(self.deficit >= 0.0)
        o.subject_to(self.a >= self.a_lo)
        o.subject_to(self.a <= self.a_hi)

        dp = self.p[1:] - self.p[:-1]
        o.minimize(
            self.t[-1]
            + options.smoothing * ca.sumsqr(dp) / n
            + 1e-3 * ca.sum1(self.b) / n
            + _SPLIT_PENALTY * ca.sum1(self.excess + self.deficit) / n
            + _POSITION_PENALTY * ca.sumsqr(self.a[1:] - self.a[:-1]) / n
        )
        o.solver(
            "ipopt",
            {"print_time": False},
            {
                "print_level": 5 if options.verbose else 0,
                "sb": "yes",
                "max_iter": options.max_iter,
                # the guess is near-feasible, so start with a small barrier parameter
                "mu_init": 1e-3,
                # riding at CP with W'bal pinned at zero is a singular arc; accept a
                # slightly loose optimum there rather than iterating to the limit
                "tol": 1e-7,
                "constr_viol_tol": 1e-8,
                "acceptable_tol": 1e-4,
                "acceptable_iter": 10,
            },
        )

    def solve(self, head, guess, climbing=None) -> dict[str, np.ndarray]:
        """Solve with positions free (``climbing=None``) or fixed to ``climbing``."""
        o, a = self.opti, self.athlete
        n = len(head)
        o.set_value(self.head, head)
        o.set_value(self.a_lo, np.zeros(n) if climbing is None else climbing.astype(float))
        o.set_value(self.a_hi, np.ones(n) if climbing is None else climbing.astype(float))
        o.set_initial(self.a, guess["a"] if climbing is None else climbing.astype(float))
        o.set_initial(self.v, guess["v"])
        o.set_initial(self.p, guess["p"] / a.cp)
        o.set_initial(self.w, guess["w"] / a.w_prime)
        o.set_initial(self.g, guess["g"] / a.glycogen)
        o.set_initial(self.t, guess["t"] / self.t_ref)
        o.set_initial(self.b, guess["b"] / self.weight)
        cp_rel = a.aero_cp_factor + guess["a"] * (1.0 - a.aero_cp_factor)
        rel = guess["p"] / a.cp - cp_rel
        o.set_initial(self.excess, np.maximum(rel, 0.0))
        o.set_initial(self.deficit, np.maximum(-rel, 0.0))

        try:
            result = o.solve()
        except RuntimeError:  # CasADi raises on anything but full success
            result = o.debug
        status = result.stats()["return_status"]
        if status not in ("Solve_Succeeded", "Solved_To_Acceptable_Level"):
            raise InfeasibleError(
                f"IPOPT did not converge ({status}). The reserves requested at the finish may be "
                "unreachable for this athlete and course; try lowering w_prime_final/glycogen_final."
            )

        def value(x):
            return np.atleast_1d(np.asarray(result.value(x), dtype=float)).ravel()

        return dict(
            v=value(self.v),
            p=value(self.p) * a.cp,
            w=value(self.w) * a.w_prime,
            g=value(self.g) * a.glycogen,
            t=value(self.t) * self.t_ref,
            b=value(self.b) * self.weight,
            a=np.clip(value(self.a), 0.0, 1.0),
        )


def _head_wind(course: Course, wind: WindField | None, race: RaceSpec, node_time: np.ndarray) -> np.ndarray:
    if wind is None:
        return np.zeros(course.n)
    t_mid = 0.5 * (node_time[:-1] + node_time[1:])
    t_abs = None if race.start_time is None else race.start_time.timestamp() + t_mid
    u, v = wind.sample(course.lat_mid, course.lon_mid, t_abs)
    return race.wind_height_factor * head_wind_component(u, v, course.heading)


def _choose_positions(course: Course, speed: np.ndarray, race: RaceSpec) -> np.ndarray:
    """Sit up on steep grades or when slow enough that aerodynamics barely matter."""
    v_mid = 0.5 * (speed[:-1] + speed[1:])
    climbing = (course.grade >= race.climb_grade) | (v_mid < race.aero_min_speed)
    # drop single-segment flickers between positions
    return median_filter(climbing.astype(np.int8), size=3, mode="nearest").astype(bool)


def _round_positions(course: Course, a: np.ndarray, min_run: float) -> np.ndarray:
    """Round relaxed positions to aero/upright, then merge stints shorter than ``min_run`` metres.

    The shortest offending stint is flipped first, so a brief upright section inside
    a long aero one (or vice versa) is absorbed by its surroundings.
    """
    upright = a > 0.5
    length = course.seg_length
    while True:
        edges = np.flatnonzero(np.diff(upright.astype(np.int8))) + 1
        starts, ends = np.concatenate([[0], edges]), np.concatenate([edges, [len(upright)]])
        if len(starts) == 1:
            return upright
        runs = np.array([length[s:e].sum() for s, e in zip(starts, ends)])
        shortest = int(np.argmin(runs))
        if runs[shortest] >= min_run:
            return upright
        upright[starts[shortest]:ends[shortest]] = ~upright[starts[shortest]]


def _cda_for(athlete: Athlete, climbing: np.ndarray) -> np.ndarray:
    return np.where(climbing, athlete.cda_climb, athlete.cda_aero)


def _cp_for(athlete: Athlete, climbing: np.ndarray) -> np.ndarray:
    return np.where(climbing, athlete.cp, athlete.cp * athlete.aero_cp_factor)
