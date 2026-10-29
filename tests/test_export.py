import io
import json
from datetime import date

import numpy as np
import pytest

from wattoptimizer import Athlete, ConstantWind, RaceSpec, export, optimize
from wattoptimizer.simulate import meets_reserves


@pytest.fixture(scope="module")
def plan(hilly_out_and_back):
    return optimize(hilly_out_and_back, Athlete(), RaceSpec(w_prime_final=0.2), wind=ConstantWind(4.0, 90.0))


@pytest.fixture(scope="module")
def stepped(plan):
    return export.plan_steps(plan, n_steps=8, min_length=400.0)


def test_split_points_cover_the_course(plan):
    step_of = export.split_points(plan, n_steps=8, min_length=400.0)
    assert step_of[0] == 0 and np.all(np.diff(step_of) >= 0) and np.all(np.diff(step_of) <= 1)
    assert step_of.max() + 1 == 8
    lengths = np.bincount(step_of, plan.course.seg_length)
    assert lengths.min() >= 400.0 - 1e-6


def test_steps_meet_reserves_when_ridden_exactly(plan, stepped):
    steps, check = stepped
    assert check.feasible
    assert meets_reserves(check.sim, plan.athlete, plan.race)
    # riding fewer, constant steps cannot beat the continuous optimum
    assert check.time_cost_s > -0.5
    assert steps[0].start_m == 0.0
    assert steps[-1].end_m == pytest.approx(plan.course.total_distance)
    assert all(s.low_w <= s.target_w <= s.high_w for s in steps)


def test_steps_beat_naive_averaging(plan, stepped):
    """Re-optimising the step watts is better than averaging the plan within each step."""
    steps, check = stepped
    step_of = export.split_points(plan, n_steps=8, min_length=400.0)
    t = plan.seg_time
    means = np.bincount(step_of, plan.power * t) / np.bincount(step_of, t)
    naive = [export.Step(s.start_m, s.end_m, float(m), float(m), float(m), s.upright) for s, m in zip(steps, means)]
    _, naive_check = export.fit_to_reserves(plan, naive)
    assert check.sim.total_time <= naive_check.sim.total_time + 0.5


def test_workout_text_round_trips(plan, stepped):
    steps, _ = stepped
    text = export.workout_text(steps)
    parsed = export.parse_workout_text(text)
    assert len(parsed) == len(steps)
    assert sum(p[0] for p in parsed) == pytest.approx(plan.course.total_distance, abs=10.0)
    for (length, lo, hi, upright), s in zip(parsed, steps):
        assert (lo, hi, upright) == (s.low_w, s.high_w, s.upright)
        assert length == pytest.approx(s.length_m, abs=10.0)
    # metres must never be written as a bare "m", which intervals.icu reads as minutes
    assert all(line.split()[-2].endswith("km") for line in text.splitlines())


def _doc_for(steps, *, use_duration=False, units="w"):
    edges = np.round(np.array([steps[0].start_m] + [s.end_m for s in steps]) / 10.0) * 10.0
    doc_steps = []
    for s, length in zip(steps, np.diff(edges)):
        step = {"power": {"start": s.low_w, "end": s.high_w, "units": units}}
        step["duration" if use_duration else "distance"] = float(length)
        doc_steps.append(step)
    return {"id": 42, "workout_doc": {"steps": doc_steps}}


def test_verify_event_accepts_a_faithful_parse(stepped):
    steps, _ = stepped
    assert export.verify_event(_doc_for(steps), steps) == []


@pytest.mark.parametrize(
    "event, expected",
    [
        (lambda s: _doc_for(s, use_duration=True), "no distance"),
        (lambda s: _doc_for(s, units="%ftp"), "expected watts"),
        (lambda s: {"workout_doc": {"steps": _doc_for(s)["workout_doc"]["steps"][:-1]}}, "parsed"),
        (lambda s: {"id": 1}, "no parsed workout"),
    ],
)
def test_verify_event_catches_misparses(stepped, event, expected):
    steps, _ = stepped
    problems = export.verify_event(event(steps), steps)
    assert problems and any(expected in p for p in problems)


def test_upload_sends_authenticated_upsert(monkeypatch, plan, stepped):
    steps, check = stepped
    sent = {}

    def fake_urlopen(req, timeout):
        sent["url"], sent["method"] = req.full_url, req.get_method()
        sent["auth"] = req.get_header("Authorization")
        sent["agent"] = req.get_header("User-agent")
        sent["body"] = json.loads(req.data)
        return io.BytesIO(json.dumps([_doc_for(steps)]).encode())

    monkeypatch.setattr(export, "urlopen", fake_urlopen)
    payload = export.event_payload(plan, steps, check, date(2026, 7, 12))
    event = export.upload_intervals(payload, "secret", "i123")

    assert sent["method"] == "POST"
    assert sent["url"] == "https://intervals.icu/api/v1/athlete/i123/events/bulk?upsert=true"
    assert sent["auth"] == "Basic QVBJX0tFWTpzZWNyZXQ="  # API_KEY:secret
    assert sent["agent"].startswith("wattoptimizer/")  # urllib's default is blocked by Cloudflare
    assert sent["body"][0]["category"] == "WORKOUT"
    assert sent["body"][0]["start_date_local"] == "2026-07-12T00:00:00"
    assert sent["body"][0]["external_id"].endswith("2026-07-12")
    assert export.verify_event(event, steps) == []
