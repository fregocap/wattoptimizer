"""Export a plan as a workout of distance-based power steps, and upload it to intervals.icu.

Three checks make an export trustworthy:

1. ``plan_steps``: the step watts come from re-solving the optimal control problem
   with power constant within each step; the rounded steps are then *re-ridden* in
   the simulator (and lowered if needed) so riding them exactly still meets the
   race's W'bal and glycogen reserves.
2. ``workout_text`` / ``parse_workout_text``: the text round-trips to the same steps
   and its distances add up to the course length.
3. ``verify_event``: after upload, intervals.icu's own parse of the workout is
   compared step by step with what was sent.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, replace
from datetime import date
from urllib.request import Request, urlopen

import numpy as np

from .optimizer import Plan, SolverOptions, optimize_steps
from .simulate import SimResult, meets_reserves, simulate

INTERVALS_API = "https://intervals.icu/api/v1"
_STEP_RE = re.compile(r"^-\s*(?P<cue>.*?)\s*(?P<km>\d+(?:\.\d+)?)km\s+(?P<lo>\d+)-(?P<hi>\d+)w\s*$")


@dataclass(frozen=True)
class Step:
    start_m: float
    end_m: float
    target_w: float
    low_w: float
    high_w: float
    upright: bool

    @property
    def length_m(self) -> float:
        return self.end_m - self.start_m


@dataclass
class StepCheck:
    """Outcome of riding the steps exactly, compared with the continuous plan."""

    sim: SimResult
    feasible: bool
    offset_w: float  # how much every step was lowered to meet the reserves
    time_cost_s: float  # stepped finish time minus the plan's


def split_points(plan: Plan, n_steps: int = 20, min_length: float = 400.0) -> np.ndarray:
    """Step index of every segment, for ``n_steps`` steps of at least ``min_length`` metres.

    Boundaries are chosen by dynamic programming to minimise the time-weighted squared
    deviation from the plan's power, i.e. they fall where the plan changes effort.
    """
    dist = plan.course.distance
    n = plan.course.n
    w, p = plan.seg_time, plan.power
    s0, s1, s2 = (np.concatenate([[0.0], np.cumsum(x)]) for x in (w, w * p, w * p * p))
    k_max = max(1, min(n_steps, n, int(dist[-1] // min_length)))

    best = np.full((k_max + 1, n + 1), np.inf)
    split = np.zeros((k_max + 1, n + 1), dtype=int)
    best[0, 0] = 0.0
    for k in range(1, k_max + 1):
        for j in range(k, n + 1):
            i = np.arange(j)
            total = best[k - 1, :j] + s2[j] - s2[i] - (s1[j] - s1[i]) ** 2 / (s0[j] - s0[i])
            if k > 1 or j < n:  # a course shorter than min_length is still one step
                total[dist[j] - dist[:j] < min_length] = np.inf
            split[k, j] = int(np.argmin(total))
            best[k, j] = total[split[k, j]]
    k = max(kk for kk in range(1, k_max + 1) if np.isfinite(best[kk, n]))

    bounds = [n]
    for kk in range(k, 0, -1):
        bounds.append(split[kk, bounds[-1]])
    step_of = np.empty(n, dtype=int)
    for idx, (a, b) in enumerate(zip(bounds[::-1][:-1], bounds[::-1][1:])):
        step_of[a:b] = idx
    return step_of


def plan_steps(
    plan: Plan,
    *,
    n_steps: int = 20,
    min_length: float = 400.0,
    band_w: float = 5.0,
    options: SolverOptions | None = None,
) -> tuple[list[Step], StepCheck]:
    """Turn a plan into ``n_steps`` distance-based power steps that are safe to ride exactly.

    The step watts are not averages of the plan: the optimal control problem is
    re-solved with power held constant within each step (averaging pulses over
    rollers can run W'bal dry). The rounded steps are then re-ridden in the simulator
    and, if needed, lowered 1 W at a time until they meet the reserves.
    """
    step_of = split_points(plan, n_steps, min_length)
    stepped = optimize_steps(plan, step_of, options)
    dist, t = plan.course.distance, stepped.seg_time
    steps = []
    for idx in range(int(step_of.max()) + 1):
        seg = np.flatnonzero(step_of == idx)
        target = float(np.floor(stepped.power[seg].mean() + 1e-6))
        upright = float((t[seg] * plan.climbing[seg]).sum() / t[seg].sum()) > 0.5
        steps.append(Step(dist[seg[0]], dist[seg[-1] + 1], target, max(target - band_w, 0.0), target + band_w, upright))
    return fit_to_reserves(plan, steps)


def stepped_power(plan: Plan, steps: list[Step]) -> np.ndarray:
    """Per-segment power when riding ``steps`` exactly (segments assigned by midpoint)."""
    mid = 0.5 * (plan.course.distance[:-1] + plan.course.distance[1:])
    ends = np.array([s.end_m for s in steps])
    idx = np.minimum(np.searchsorted(ends, mid), len(steps) - 1)
    return np.array([s.target_w for s in steps])[idx]


def check_steps(plan: Plan, steps: list[Step], offset_w: float = 0.0) -> StepCheck:
    """Re-ride the steps (lowered by ``offset_w``) under the plan's wind, positions and corners."""
    power = np.maximum(stepped_power(plan, steps) - offset_w, 0.0)
    sim = simulate(
        plan.course, plan.athlete, plan.race, power,
        head_wind=plan.head_wind, cda=plan.cda, cp=plan.cp_segment, v_max=plan.v_max,
    )
    return StepCheck(sim, meets_reserves(sim, plan.athlete, plan.race), offset_w, sim.total_time - plan.total_time)


def fit_to_reserves(plan: Plan, steps: list[Step], max_offset_w: float = 30.0) -> tuple[list[Step], StepCheck]:
    """Lower every step by the smallest whole number of watts that makes the stepped ride meet the reserves."""
    for offset in range(int(max_offset_w) + 1):
        check = check_steps(plan, steps, offset)
        if check.feasible:
            lowered = [
                replace(s, target_w=max(s.target_w - offset, 0.0), low_w=max(s.low_w - offset, 0.0),
                        high_w=max(s.high_w - offset, 0.0))
                for s in steps
            ]
            return lowered, check
    return steps, check_steps(plan, steps)


def workout_text(steps: list[Step]) -> str:
    """intervals.icu workout text: one ``- <cue> <km>km <lo>-<hi>w`` line per step.

    Step boundaries are rounded to 10 m cumulatively, so the lengths always add up to
    the rounded course length. Lengths are written in km because intervals.icu reads
    a bare ``m`` as minutes.
    """
    edges = np.round(np.array([steps[0].start_m] + [s.end_m for s in steps]) / 10.0) * 10.0
    lines = []
    for s, a, b in zip(steps, edges[:-1], edges[1:]):
        cue = "Sit up " if s.upright else ""
        lines.append(f"- {cue}{(b - a) / 1000:.2f}km {s.low_w:.0f}-{s.high_w:.0f}w")
    return "\n".join(lines)


def parse_workout_text(text: str) -> list[tuple[float, float, float, bool]]:
    """Parse lines written by :func:`workout_text` into (length_m, low_w, high_w, upright)."""
    out = []
    for line in text.splitlines():
        if not line.startswith("-"):
            continue
        m = _STEP_RE.match(line.strip())
        if not m:
            raise ValueError(f"unrecognised step line: {line!r}")
        out.append((float(m["km"]) * 1000.0, float(m["lo"]), float(m["hi"]), "sit up" in m["cue"].lower()))
    return out


def event_payload(plan: Plan, steps: list[Step], check: StepCheck, day: date, *, name: str | None = None) -> dict:
    """A planned-workout event for intervals.icu's ``events/bulk`` endpoint."""
    name = name or plan.course.name
    slug = re.sub(r"[^a-z0-9]+", "-", plan.course.name.lower()).strip("-")
    ridden = check.sim.time
    avg = (stepped_power(plan, steps) * np.diff(ridden)).sum() / ridden[-1]
    header = (
        f"WattOptimizer plan: {ridden[-1] / 60:.0f} min, {avg:.0f} W average.\n"
        "Steps advance by distance; hold the band, sit up where marked.\n\n"
    )
    return {
        "category": "WORKOUT",
        "type": "Ride",
        "name": name,
        "start_date_local": f"{day.isoformat()}T00:00:00",
        "description": header + workout_text(steps),
        "external_id": f"wattoptimizer-{slug}-{day.isoformat()}",
    }


def upload_intervals(payload: dict, api_key: str, athlete_id: str = "0", *, timeout: float = 30.0) -> dict:
    """Create or update (by ``external_id``) the event and return it as stored by intervals.icu.

    ``athlete_id`` "0" means the athlete that owns the API key.
    """
    url = f"{INTERVALS_API}/athlete/{athlete_id}/events/bulk?upsert=true"
    events = _request("POST", url, api_key, [payload], timeout)
    event = events[0] if isinstance(events, list) else events
    if "workout_doc" not in event and "id" in event:
        event = _request("GET", f"{INTERVALS_API}/athlete/{athlete_id}/events/{event['id']}", api_key, None, timeout)
    return event


def verify_event(event: dict, steps: list[Step], *, distance_tol: float = 0.02) -> list[str]:
    """Compare intervals.icu's parsed workout with ``steps``; returns a list of problems (empty = OK)."""
    doc = event.get("workout_doc")
    if not isinstance(doc, dict) or not isinstance(doc.get("steps"), list):
        return ["intervals.icu returned no parsed workout (workout_doc); check the event in the web app"]
    parsed = _flatten(doc["steps"])
    if len(parsed) != len(steps):
        return [f"intervals.icu parsed {len(parsed)} steps, expected {len(steps)}"]

    problems = []
    edges = np.round(np.array([steps[0].start_m] + [s.end_m for s in steps]) / 10.0) * 10.0
    for k, (got, want, length) in enumerate(zip(parsed, steps, np.diff(edges)), start=1):
        dist = got.get("distance")
        if not isinstance(dist, (int, float)):
            problems.append(f"step {k}: no distance (parsed as {_describe(got)}), so it would not advance by distance")
        elif abs(dist - length) > distance_tol * length:
            problems.append(f"step {k}: distance {dist:.0f} m, expected {length:.0f} m")
        lo, hi, units = _power_range(got.get("power"))
        if lo is None:
            problems.append(f"step {k}: no power target (parsed as {_describe(got)})")
        elif units not in (None, "w", "watts", "power"):
            problems.append(f"step {k}: power in {units!r}, expected watts")
        elif abs(lo - want.low_w) > 0.5 or abs(hi - want.high_w) > 0.5:
            problems.append(f"step {k}: power {lo:.0f}-{hi:.0f} W, expected {want.low_w:.0f}-{want.high_w:.0f} W")
    return problems


def _flatten(steps: list) -> list[dict]:
    out = []
    for s in steps:
        if isinstance(s, dict) and isinstance(s.get("steps"), list):  # repeat block
            out.extend(_flatten(s["steps"]) * int(s.get("reps", 1)))
        elif isinstance(s, dict):
            out.append(s)
    return out


def _power_range(power) -> tuple[float | None, float | None, str | None]:
    if not isinstance(power, dict):
        return None, None, None
    units = power.get("units")
    units = units.lower() if isinstance(units, str) else None
    if "start" in power and "end" in power:
        return float(power["start"]), float(power["end"]), units
    if "value" in power:
        return float(power["value"]), float(power["value"]), units
    return None, None, units


def _describe(step: dict) -> str:
    keys = {k: step[k] for k in ("duration", "distance", "power", "text") if k in step}
    return json.dumps(keys)


def _request(method: str, url: str, api_key: str, body, timeout: float):
    token = base64.b64encode(f"API_KEY:{api_key}".encode()).decode()
    data = None if body is None else json.dumps(body).encode()
    req = Request(url, data=data, method=method, headers={
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    with urlopen(req, timeout=timeout) as response:
        return json.load(response)
